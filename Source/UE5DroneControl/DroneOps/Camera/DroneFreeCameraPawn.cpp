#include "DroneOps/Camera/DroneFreeCameraPawn.h"

#include "Camera/CameraComponent.h"
#include "Components/SceneComponent.h"

ADroneFreeCameraPawn::ADroneFreeCameraPawn()
{
	PrimaryActorTick.bCanEverTick = true;

	SceneRoot = CreateDefaultSubobject<USceneComponent>(TEXT("SceneRoot"));
	SetRootComponent(SceneRoot);

	CameraComponent = CreateDefaultSubobject<UCameraComponent>(TEXT("FreeCamera"));
	CameraComponent->SetupAttachment(SceneRoot);
	CameraComponent->bUsePawnControlRotation = false;
}

void ADroneFreeCameraPawn::BeginPlay()
{
	Super::BeginPlay();
}

void ADroneFreeCameraPawn::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	ApplyLookInput();

	const FVector MoveInput = ConsumeMovementInput();
	const FRotator YawRotation(0.0f, GetActorRotation().Yaw, 0.0f);

	FVector DesiredDirection = YawRotation.RotateVector(FVector(MoveInput.X, MoveInput.Y, 0.0f));
	DesiredDirection += FVector::UpVector * MoveInput.Z;
	DesiredDirection = DesiredDirection.GetClampedToMaxSize(1.0f);

	const FVector DesiredVelocity = DesiredDirection * MoveSpeed;
	CurrentVelocity = FMath::VInterpTo(CurrentVelocity, DesiredVelocity, DeltaSeconds, Acceleration);

	AddActorWorldOffset(CurrentVelocity * DeltaSeconds, true);
}

void ADroneFreeCameraPawn::AddForwardInput(float Value)
{
	PendingMoveInput.X = FMath::Clamp(Value, -1.0f, 1.0f);
}

void ADroneFreeCameraPawn::AddRightInput(float Value)
{
	PendingMoveInput.Y = FMath::Clamp(Value, -1.0f, 1.0f);
}

void ADroneFreeCameraPawn::AddUpInput(float Value)
{
	PendingMoveInput.Z = FMath::Clamp(Value, -1.0f, 1.0f);
}

void ADroneFreeCameraPawn::AddYawInput(float Value)
{
	PendingYawInput = Value;
}

void ADroneFreeCameraPawn::AddPitchInput(float Value)
{
	PendingPitchInput = Value;
}

void ADroneFreeCameraPawn::SnapToCameraTransform(const FVector& NewLocation, const FRotator& NewRotation)
{
	SetActorLocation(NewLocation);
	SetActorRotation(NewRotation);
	CurrentVelocity = FVector::ZeroVector;
	PendingMoveInput = FVector::ZeroVector;
	PendingYawInput = 0.0f;
	PendingPitchInput = 0.0f;
}

FVector ADroneFreeCameraPawn::ConsumeMovementInput()
{
	const FVector MoveInput = PendingMoveInput;
	PendingMoveInput = FVector::ZeroVector;
	return MoveInput;
}

void ADroneFreeCameraPawn::ApplyLookInput()
{
	if (FMath::IsNearlyZero(PendingYawInput) && FMath::IsNearlyZero(PendingPitchInput))
	{
		return;
	}

	FRotator NewRotation = GetActorRotation();
	NewRotation.Yaw += PendingYawInput * LookSensitivity;
	NewRotation.Pitch = FMath::ClampAngle(NewRotation.Pitch - (PendingPitchInput * LookSensitivity), MinPitch, MaxPitch);
	NewRotation.Roll = 0.0f;

	SetActorRotation(NewRotation);

	PendingYawInput = 0.0f;
	PendingPitchInput = 0.0f;
}
