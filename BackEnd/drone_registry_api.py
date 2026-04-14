#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import json
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "drone_registry_config.yaml"
DEFAULT_STORAGE_PATH = BASE_DIR / "data" / "drone_registry.json"


@dataclass(frozen=True)
class SlotMapping:
    slot: int
    control_port: int
    telemetry_port: int
    topic_prefix: str
    mavlink_system_id: int
    bit_index: int


DEFAULT_SLOT_MAPPINGS: dict[int, SlotMapping] = {
    1: SlotMapping(1, 8889, 8888, "/px4_1", 2, 0),
    2: SlotMapping(2, 8891, 8890, "/px4_2", 3, 1),
    3: SlotMapping(3, 8893, 8892, "/px4_3", 4, 2),
    4: SlotMapping(4, 8895, 8894, "/px4_4", 5, 3),
    5: SlotMapping(5, 8897, 8896, "/px4_5", 6, 4),
    # 8899 is reserved by multi_ue_controller.py, so slot 6 skips to 8901.
    6: SlotMapping(6, 8901, 8900, "/px4_6", 7, 5),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ip_port(value: str) -> tuple[str, int]:
    text = value.strip()
    if not text or ":" not in text:
        raise ValueError("ip_port must use the form 'ip:port'")

    host, port_text = text.rsplit(":", 1)
    host = host.strip()
    port_text = port_text.strip()
    if not host or not port_text:
        raise ValueError("ip_port must use the form 'ip:port'")

    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("port must be an integer") from exc

    return host, port


def _validate_ip(value: str) -> str:
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"invalid IP address: {value}") from exc
    return value


class DroneCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    ip: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    ip_port: str | None = Field(default=None, alias="ipPort")
    video_stream_url: str | None = Field(default=None, alias="videoStreamUrl")
    slot: int | None = Field(default=None, ge=1, le=6)
    channel: int | None = Field(default=None, ge=1, le=6)
    port_number: int | None = Field(default=None, alias="portNumber", ge=1, le=6)


class DroneUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    ip: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    ip_port: str | None = Field(default=None, alias="ipPort")
    video_stream_url: str | None = Field(default=None, alias="videoStreamUrl")
    slot: int | None = Field(default=None, ge=1, le=6)
    channel: int | None = Field(default=None, ge=1, le=6)
    port_number: int | None = Field(default=None, alias="portNumber", ge=1, le=6)


class JsonRegistryStore:
    def __init__(self, storage_path: Path) -> None:
        self.storage_path = storage_path

    def load(self) -> dict[str, Any]:
        if not self.storage_path.exists():
            return {"version": 1, "next_id": 1, "drones": []}

        raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
        return {
            "version": int(raw.get("version", 1)),
            "next_id": int(raw.get("next_id", 1)),
            "drones": list(raw.get("drones", [])),
        }

    def save(self, state: dict[str, Any]) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.storage_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temp_path.replace(self.storage_path)


class DroneRegistryService:
    def __init__(self, storage_path: Path, slot_mappings: dict[int, SlotMapping]) -> None:
        self._lock = threading.RLock()
        self._store = JsonRegistryStore(storage_path)
        self._slot_mappings = slot_mappings
        self._command_queues: dict[int, deque[dict[str, Any]]] = {}
        self._runtime_states: dict[int, dict[str, Any]] = {}

        persisted = self._store.load()
        self._next_id = max(1, int(persisted["next_id"]))
        self._drones_by_id: dict[int, dict[str, Any]] = {}

        for record in persisted["drones"]:
            drone_id = int(record["id"])
            self._drones_by_id[drone_id] = dict(record)
            self._ensure_runtime_state(self._drones_by_id[drone_id])

    def list_drones(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._serialize_drone(drone_id) for drone_id in sorted(self._drones_by_id)]

    def create_drone(self, payload: DroneCreateRequest) -> dict[str, Any]:
        normalized = self._normalize_create_payload(payload)

        with self._lock:
            self._ensure_unique_name(normalized["name"])
            self._ensure_slot_available(normalized["slot"])

            mapping = self._get_slot_mapping(normalized["slot"])
            now = _utc_now()
            drone_id = self._next_id
            self._next_id += 1

            record = {
                "id": drone_id,
                "name": normalized["name"],
                "model": normalized["model"],
                "ip": normalized["ip"],
                "port": normalized["port"],
                "ip_port": f"{normalized['ip']}:{normalized['port']}",
                "video_stream_url": normalized["video_stream_url"],
                "slot": normalized["slot"],
                "udp_port": mapping.control_port,
                "control_port": mapping.control_port,
                "telemetry_port": mapping.telemetry_port,
                "topic_prefix": mapping.topic_prefix,
                "mavlink_system_id": mapping.mavlink_system_id,
                "bit_index": mapping.bit_index,
                "created_at": now,
                "updated_at": now,
            }

            self._drones_by_id[drone_id] = record
            self._ensure_runtime_state(record)
            self._persist_locked()
            return self._serialize_drone(drone_id)

    def update_drone(self, drone_id: int, payload: DroneUpdateRequest) -> dict[str, Any]:
        with self._lock:
            current = self._get_record(drone_id)
            merged = self._normalize_update_payload(payload, current)

            self._ensure_unique_name(merged["name"], exclude_id=drone_id)
            self._ensure_slot_available(merged["slot"], exclude_id=drone_id)

            mapping = self._get_slot_mapping(merged["slot"])
            updated = dict(current)
            updated.update(
                {
                    "name": merged["name"],
                    "model": merged["model"],
                    "ip": merged["ip"],
                    "port": merged["port"],
                    "ip_port": f"{merged['ip']}:{merged['port']}",
                    "video_stream_url": merged["video_stream_url"],
                    "slot": merged["slot"],
                    "udp_port": mapping.control_port,
                    "control_port": mapping.control_port,
                    "telemetry_port": mapping.telemetry_port,
                    "topic_prefix": mapping.topic_prefix,
                    "mavlink_system_id": mapping.mavlink_system_id,
                    "bit_index": mapping.bit_index,
                    "updated_at": _utc_now(),
                }
            )

            self._drones_by_id[drone_id] = updated
            self._ensure_runtime_state(updated)
            self._persist_locked()
            return self._serialize_drone(drone_id)

    def delete_drone(self, drone_id: int) -> dict[str, Any]:
        with self._lock:
            record = self._get_record(drone_id)
            serialized = self._serialize_drone(drone_id)

            del self._drones_by_id[drone_id]
            self._command_queues.pop(drone_id, None)
            self._runtime_states.pop(drone_id, None)
            self._persist_locked()

            return {
                "deleted": True,
                "id": drone_id,
                "name": record["name"],
                "slot_released": record["slot"],
                "drone": serialized,
            }

    def get_debug_state(self, drone_id: int) -> dict[str, Any]:
        with self._lock:
            record = self._get_record(drone_id)
            runtime = self._runtime_states[drone_id]
            queue = list(self._command_queues[drone_id])

            return {
                "id": drone_id,
                "name": record["name"],
                "slot": record["slot"],
                "udp_port": record["udp_port"],
                "control_port": record["control_port"],
                "telemetry_port": record["telemetry_port"],
                "topic_prefix": record["topic_prefix"],
                "mavlink_system_id": record["mavlink_system_id"],
                "bit_index": record["bit_index"],
                "runtime_state": dict(runtime),
                "queue_depth": len(queue),
                "queued_commands": queue,
            }

    def _normalize_create_payload(self, payload: DroneCreateRequest) -> dict[str, Any]:
        name = payload.name.strip()
        model = payload.model.strip()
        video_stream_url = (payload.video_stream_url or "").strip()
        slot = self._resolve_slot(payload.slot, payload.channel, payload.port_number, current_slot=None, required=True)
        ip, port = self._resolve_endpoint(
            payload.ip,
            payload.port,
            payload.ip_port,
            current_ip=None,
            current_port=None,
            require_complete=True,
        )

        if not name:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="name must not be blank")
        if not model:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="model must not be blank")
        if not video_stream_url:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="video_stream_url is required",
            )

        return {
            "name": name,
            "model": model,
            "ip": ip,
            "port": port,
            "video_stream_url": video_stream_url,
            "slot": slot,
        }

    def _normalize_update_payload(self, payload: DroneUpdateRequest, current: dict[str, Any]) -> dict[str, Any]:
        fields = set(payload.model_fields_set)
        if not fields:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="no fields provided")

        name = current["name"]
        model = current["model"]
        video_stream_url = current["video_stream_url"]

        if "name" in fields:
            name = (payload.name or "").strip()
            if not name:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="name must not be blank")

        if "model" in fields:
            model = (payload.model or "").strip()
            if not model:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="model must not be blank")

        if "video_stream_url" in fields:
            video_stream_url = (payload.video_stream_url or "").strip()
            if not video_stream_url:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="video_stream_url must not be blank",
                )

        ip, port = self._resolve_endpoint(
            payload.ip if "ip" in fields else None,
            payload.port if "port" in fields else None,
            payload.ip_port if "ip_port" in fields else None,
            current_ip=current["ip"],
            current_port=current["port"],
            require_complete=True,
        )

        slot = self._resolve_slot(
            payload.slot if "slot" in fields else None,
            payload.channel if "channel" in fields else None,
            payload.port_number if "port_number" in fields else None,
            current_slot=current["slot"],
            required=True,
        )

        return {
            "name": name,
            "model": model,
            "ip": ip,
            "port": port,
            "video_stream_url": video_stream_url,
            "slot": slot,
        }

    def _resolve_endpoint(
        self,
        ip: str | None,
        port: int | None,
        ip_port: str | None,
        *,
        current_ip: str | None,
        current_port: int | None,
        require_complete: bool,
    ) -> tuple[str, int]:
        resolved_ip = current_ip
        resolved_port = current_port

        if ip_port is not None:
            parsed_ip, parsed_port = _parse_ip_port(ip_port)
            if ip is not None and ip.strip() != parsed_ip:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="ip conflicts with ip_port",
                )
            if port is not None and port != parsed_port:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="port conflicts with ip_port",
                )
            resolved_ip = parsed_ip
            resolved_port = parsed_port

        if ip is not None:
            resolved_ip = ip.strip()
        if port is not None:
            resolved_port = port

        if require_complete and (not resolved_ip or resolved_port is None):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="ip and port are required",
            )

        if not resolved_ip or resolved_port is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="ip and port are required",
            )

        try:
            resolved_ip = _validate_ip(resolved_ip)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

        if resolved_port < 1 or resolved_port > 65535:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="port out of range")

        return resolved_ip, resolved_port

    def _resolve_slot(
        self,
        slot: int | None,
        channel: int | None,
        port_number: int | None,
        *,
        current_slot: int | None,
        required: bool,
    ) -> int:
        values = [value for value in (slot, channel, port_number) if value is not None]
        if values and len(set(values)) > 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="slot, channel and portNumber must match when provided together",
            )

        resolved = values[0] if values else current_slot
        if required and resolved is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="slot is required")
        if resolved is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="slot is required")
        if resolved not in self._slot_mappings:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"slot {resolved} is not configured")

        return resolved

    def _get_slot_mapping(self, slot: int) -> SlotMapping:
        return self._slot_mappings[slot]

    def _ensure_unique_name(self, name: str, exclude_id: int | None = None) -> None:
        target = name.casefold()
        for drone_id, record in self._drones_by_id.items():
            if exclude_id is not None and drone_id == exclude_id:
                continue
            if record["name"].casefold() == target:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"drone name '{name}' already exists")

    def _ensure_slot_available(self, slot: int, exclude_id: int | None = None) -> None:
        for drone_id, record in self._drones_by_id.items():
            if exclude_id is not None and drone_id == exclude_id:
                continue
            if record["slot"] == slot:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"slot {slot} is already in use")

    def _get_record(self, drone_id: int) -> dict[str, Any]:
        try:
            return self._drones_by_id[drone_id]
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"drone {drone_id} not found") from exc

    def _ensure_runtime_state(self, record: dict[str, Any]) -> None:
        drone_id = int(record["id"])
        self._command_queues.setdefault(drone_id, deque())
        runtime = self._runtime_states.setdefault(
            drone_id,
            {
                "connection_status": "unknown",
                "last_seen_at": None,
                "last_command_at": None,
                "last_error": None,
            },
        )
        runtime["slot"] = record["slot"]
        runtime["udp_port"] = record["udp_port"]
        runtime["control_port"] = record["control_port"]
        runtime["telemetry_port"] = record["telemetry_port"]
        runtime["topic_prefix"] = record["topic_prefix"]

    def _serialize_drone(self, drone_id: int) -> dict[str, Any]:
        record = dict(self._get_record(drone_id))
        runtime = self._runtime_states.get(drone_id, {})
        record["connection_status"] = runtime.get("connection_status", "unknown")
        return record

    def _persist_locked(self) -> None:
        self._store.save(
            {
                "version": 1,
                "next_id": self._next_id,
                "drones": [self._drones_by_id[drone_id] for drone_id in sorted(self._drones_by_id)],
            }
        )


def _default_settings() -> dict[str, Any]:
    return {
        "host": "0.0.0.0",
        "port": 8000,
        "storage_path": DEFAULT_STORAGE_PATH,
        "slot_mappings": DEFAULT_SLOT_MAPPINGS,
    }


def _load_settings(config_path: Path | None) -> dict[str, Any]:
    settings = _default_settings()
    if config_path is None or not config_path.exists():
        return settings

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    server_cfg = raw.get("server", {})
    storage_cfg = raw.get("storage", {})
    slot_cfg = raw.get("slots", {})

    if server_cfg.get("host"):
        settings["host"] = str(server_cfg["host"])
    if server_cfg.get("port"):
        settings["port"] = int(server_cfg["port"])

    if storage_cfg.get("path"):
        storage_path = Path(storage_cfg["path"])
        if not storage_path.is_absolute():
            storage_path = config_path.parent / storage_path
        settings["storage_path"] = storage_path

    if slot_cfg:
        mappings: dict[int, SlotMapping] = {}
        for slot_key, value in slot_cfg.items():
            slot = int(slot_key)
            mappings[slot] = SlotMapping(
                slot=slot,
                control_port=int(value["control_port"]),
                telemetry_port=int(value["telemetry_port"]),
                topic_prefix=str(value["topic_prefix"]),
                mavlink_system_id=int(value["mavlink_system_id"]),
                bit_index=int(value["bit_index"]),
            )
        settings["slot_mappings"] = mappings

    return settings


def create_app(
    *,
    config_path: str | Path | None = DEFAULT_CONFIG_PATH,
    storage_path: str | Path | None = None,
) -> FastAPI:
    resolved_config_path: Path | None
    if config_path is None:
        resolved_config_path = None
    else:
        resolved_config_path = Path(config_path)
        if not resolved_config_path.is_absolute():
            resolved_config_path = BASE_DIR / resolved_config_path

    settings = _load_settings(resolved_config_path)
    if storage_path is not None:
        settings["storage_path"] = Path(storage_path)

    app = FastAPI(
        title="UE5 Drone Registry API",
        version="1.0.0",
        description="HTTP registry for drone CRUD, slot mapping and persistence.",
    )
    app.state.registry = DroneRegistryService(
        storage_path=Path(settings["storage_path"]),
        slot_mappings=settings["slot_mappings"],
    )

    def get_registry(request: Request) -> DroneRegistryService:
        return request.app.state.registry

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/drones", status_code=status.HTTP_201_CREATED)
    def create_drone(
        payload: DroneCreateRequest,
        registry: DroneRegistryService = Depends(get_registry),
    ) -> dict[str, Any]:
        return registry.create_drone(payload)

    @app.get("/api/drones")
    def list_drones(registry: DroneRegistryService = Depends(get_registry)) -> list[dict[str, Any]]:
        return registry.list_drones()

    @app.put("/api/drones/{drone_id}")
    def update_drone(
        drone_id: int,
        payload: DroneUpdateRequest,
        registry: DroneRegistryService = Depends(get_registry),
    ) -> dict[str, Any]:
        return registry.update_drone(drone_id, payload)

    @app.delete("/api/drones/{drone_id}")
    def delete_drone(
        drone_id: int,
        registry: DroneRegistryService = Depends(get_registry),
    ) -> dict[str, Any]:
        return registry.delete_drone(drone_id)

    @app.get("/api/debug/drone/{drone_id}/state")
    def debug_state(
        drone_id: int,
        registry: DroneRegistryService = Depends(get_registry),
    ) -> dict[str, Any]:
        return registry.get_debug_state(drone_id)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the drone registry API service.")
    parser.add_argument("--host", default=None, help="Host override")
    parser.add_argument("--port", type=int, default=None, help="Port override")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Config file path")
    parser.add_argument("--storage", default=None, help="Storage file override")
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else None
    settings = _load_settings(config_path if config_path and config_path.exists() else None)
    host = args.host or settings["host"]
    port = args.port or settings["port"]

    app = create_app(config_path=config_path, storage_path=args.storage)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
