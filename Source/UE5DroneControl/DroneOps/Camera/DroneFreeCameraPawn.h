#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Pawn.h"
#include "DroneFreeCameraPawn.generated.h"

class UCameraComponent;
class USceneComponent;

UCLASS()
class UE5DRONECONTROL_API ADroneFreeCameraPawn : public APawn
{
	GENERATED_BODY()

public:
	ADroneFreeCameraPawn();

	virtual void Tick(float DeltaSeconds) override;

	void AddForwardInput(float Value);
	void AddRightInput(float Value);
	void AddUpInput(float Value);
	void AddYawInput(float Value);
	void AddPitchInput(float Value);

	void SnapToCameraTransform(const FVector& NewLocation, const FRotator& NewRotation);

protected:
	virtual void BeginPlay() override;

private:
	UPROPERTY(VisibleAnywhere, Category = "Camera")
	TObjectPtr<USceneComponent> SceneRoot;

	UPROPERTY(VisibleAnywhere, Category = "Camera")
	TObjectPtr<UCameraComponent> CameraComponent;

	UPROPERTY(EditAnywhere, Category = "Free Camera", meta = (ClampMin = "0.0"))
	float MoveSpeed = 3000.0f;

	UPROPERTY(EditAnywhere, Category = "Free Camera", meta = (ClampMin = "0.1"))
	float Acceleration = 8.0f;

	UPROPERTY(EditAnywhere, Category = "Free Camera", meta = (ClampMin = "0.01"))
	float LookSensitivity = 1.0f;

	UPROPERTY(EditAnywhere, Category = "Free Camera", meta = (ClampMin = "-89.0", ClampMax = "89.0"))
	float MinPitch = -80.0f;

	UPROPERTY(EditAnywhere, Category = "Free Camera", meta = (ClampMin = "-89.0", ClampMax = "89.0"))
	float MaxPitch = 25.0f;

	UPROPERTY(VisibleAnywhere, Category = "Free Camera")
	FVector CurrentVelocity = FVector::ZeroVector;

	FVector PendingMoveInput = FVector::ZeroVector;
	float PendingYawInput = 0.0f;
	float PendingPitchInput = 0.0f;

	FVector ConsumeMovementInput();
	void ApplyLookInput();
};
