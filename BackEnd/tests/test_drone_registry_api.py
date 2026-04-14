from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

import httpx


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from drone_registry_api import create_app


class DroneRegistryApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_path = Path(self.temp_dir.name) / "registry.json"
        self.app = create_app(config_path=None, storage_path=self.storage_path)
        self.transport = httpx.ASGITransport(app=self.app)

    async def asyncSetUp(self) -> None:
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://testserver")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.temp_dir.cleanup()

    async def _register(self, *, name: str, slot: int, port: int = 14540) -> httpx.Response:
        payload = {
            "name": name,
            "model": "PX4",
            "ip": "192.168.30.104",
            "port": port,
            "videoStreamUrl": f"rtsp://192.168.30.104/live/{name.lower()}",
            "slot": slot,
        }
        return await self.client.post("/api/drones", json=payload)

    async def test_create_and_list_drones(self) -> None:
        first = await self._register(name="UAV1", slot=1)
        second = await self._register(name="UAV2", slot=2, port=14541)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json()["id"], 1)
        self.assertEqual(second.json()["id"], 2)

        listing = await self.client.get("/api/drones")
        self.assertEqual(listing.status_code, 200)
        drones = listing.json()

        self.assertEqual(len(drones), 2)
        self.assertEqual(drones[0]["name"], "UAV1")
        self.assertEqual(drones[0]["control_port"], 8889)
        self.assertEqual(drones[0]["telemetry_port"], 8888)
        self.assertEqual(drones[0]["connection_status"], "unknown")
        self.assertEqual(drones[1]["slot"], 2)

    async def test_duplicate_name_returns_conflict(self) -> None:
        first = await self._register(name="UAV1", slot=1)
        duplicate = await self._register(name="UAV1", slot=2, port=14541)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("already exists", duplicate.json()["detail"])

    async def test_update_drone(self) -> None:
        created = await self._register(name="UAV1", slot=1)
        drone_id = created.json()["id"]

        updated = await self.client.put(
            f"/api/drones/{drone_id}",
            json={
                "name": "UAV1-Renamed",
                "model": "PX4-Updated",
                "ipPort": "192.168.30.105:15555",
                "videoStreamUrl": "rtsp://192.168.30.105/live/new",
                "slot": 3,
            },
        )

        self.assertEqual(updated.status_code, 200)
        body = updated.json()
        self.assertEqual(body["name"], "UAV1-Renamed")
        self.assertEqual(body["ip_port"], "192.168.30.105:15555")
        self.assertEqual(body["slot"], 3)
        self.assertEqual(body["control_port"], 8893)

        listing = await self.client.get("/api/drones")
        self.assertEqual(listing.json()[0]["name"], "UAV1-Renamed")

    async def test_delete_clears_record_and_missing_returns_404(self) -> None:
        created = await self._register(name="UAV1", slot=1)
        drone_id = created.json()["id"]

        deleted = await self.client.delete(f"/api/drones/{drone_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.json()["deleted"])

        listing = await self.client.get("/api/drones")
        self.assertEqual(listing.json(), [])

        missing = await self.client.delete(f"/api/drones/{drone_id}")
        self.assertEqual(missing.status_code, 404)

    async def test_slot_mapping_conflict_and_debug_state(self) -> None:
        first = await self._register(name="UAV1", slot=1)
        conflict = await self._register(name="UAV2", slot=1, port=14541)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(conflict.status_code, 409)
        self.assertIn("slot 1 is already in use", conflict.json()["detail"])

        debug_state = await self.client.get("/api/debug/drone/1/state")
        self.assertEqual(debug_state.status_code, 200)
        debug_body = debug_state.json()
        self.assertEqual(debug_body["slot"], 1)
        self.assertEqual(debug_body["control_port"], 8889)
        self.assertEqual(debug_body["queue_depth"], 0)

        deleted = await self.client.delete("/api/drones/1")
        self.assertEqual(deleted.status_code, 200)

        reused = await self._register(name="UAV2", slot=1, port=14541)
        self.assertEqual(reused.status_code, 201)
        self.assertEqual(reused.json()["slot"], 1)

    async def test_persistence_survives_restart(self) -> None:
        created = await self._register(name="UAV1", slot=1)
        self.assertEqual(created.status_code, 201)
        await self.client.aclose()

        restarted_app = create_app(config_path=None, storage_path=self.storage_path)
        restarted_transport = httpx.ASGITransport(app=restarted_app)
        async with httpx.AsyncClient(transport=restarted_transport, base_url="http://testserver") as restarted:
            listing = await restarted.get("/api/drones")
            self.assertEqual(listing.status_code, 200)
            drones = listing.json()
            self.assertEqual(len(drones), 1)
            self.assertEqual(drones[0]["id"], 1)
            self.assertEqual(drones[0]["name"], "UAV1")


if __name__ == "__main__":
    unittest.main()
