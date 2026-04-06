// Copyright Epic Games, Inc. All Rights Reserved.

#include "UE5DroneControlPlayerController.h"
#include "GameFramework/Pawn.h"
#include "Blueprint/AIBlueprintHelperLibrary.h"
#include "NiagaraSystem.h"
#include "NiagaraFunctionLibrary.h"
#include "UE5DroneControlCharacter.h"
#include "RealTimeDroneReceiver.h"
#include "DroneOps/Camera/DroneFreeCameraPawn.h"
#include "Engine/World.h"
#include "EnhancedInputComponent.h"
#include "Navigation/PathFollowingComponent.h"
#include "InputActionValue.h"
#include "EnhancedInputSubsystems.h"
#include "Engine/LocalPlayer.h"
#include "Engine/PlayerCameraManager.h"
#include "UE5DroneControl.h"
#include "Kismet/GameplayStatics.h"

AUE5DroneControlPlayerController::AUE5DroneControlPlayerController()
{
	bIsTouch = false;
	bMoveToMouseCursor = false;

	// create the path following comp
	PathFollowingComponent = CreateDefaultSubobject<UPathFollowingComponent>(TEXT("Path Following Component"));

	// configure the controller
	bShowMouseCursor = true;
	DefaultMouseCursor = EMouseCursor::Default;
	CachedDestination = FVector::ZeroVector;
	FollowTime = 0.f;

	// Initialize camera switch reference
	CachedRealTimeDrone = nullptr;
	FreeCameraPawn = nullptr;
	CachedFollowTarget = nullptr;
}

void AUE5DroneControlPlayerController::SetupInputComponent()
{
	// set up gameplay key bindings
	Super::SetupInputComponent();

	// Only set up input on local player controllers
	if (IsLocalPlayerController())
	{
		// Add Input Mapping Context
		if (UEnhancedInputLocalPlayerSubsystem* Subsystem = ULocalPlayer::GetSubsystem<UEnhancedInputLocalPlayerSubsystem>(GetLocalPlayer()))
		{
			Subsystem->AddMappingContext(DefaultMappingContext, 0);
		}

		// Set up action bindings
		if (UEnhancedInputComponent* EnhancedInputComponent = Cast<UEnhancedInputComponent>(InputComponent))
		{
			// Setup mouse input events
			EnhancedInputComponent->BindAction(SetDestinationClickAction, ETriggerEvent::Started, this, &AUE5DroneControlPlayerController::OnInputStarted);
			EnhancedInputComponent->BindAction(SetDestinationClickAction, ETriggerEvent::Triggered, this, &AUE5DroneControlPlayerController::OnSetDestinationTriggered);
			EnhancedInputComponent->BindAction(SetDestinationClickAction, ETriggerEvent::Completed, this, &AUE5DroneControlPlayerController::OnSetDestinationReleased);
			EnhancedInputComponent->BindAction(SetDestinationClickAction, ETriggerEvent::Canceled, this, &AUE5DroneControlPlayerController::OnSetDestinationReleased);

			// Setup touch input events
			EnhancedInputComponent->BindAction(SetDestinationTouchAction, ETriggerEvent::Started, this, &AUE5DroneControlPlayerController::OnInputStarted);
			EnhancedInputComponent->BindAction(SetDestinationTouchAction, ETriggerEvent::Triggered, this, &AUE5DroneControlPlayerController::OnTouchTriggered);
			EnhancedInputComponent->BindAction(SetDestinationTouchAction, ETriggerEvent::Completed, this, &AUE5DroneControlPlayerController::OnTouchReleased);
			EnhancedInputComponent->BindAction(SetDestinationTouchAction, ETriggerEvent::Canceled, this, &AUE5DroneControlPlayerController::OnTouchReleased);
		}
		else
		{
			UE_LOG(LogUE5DroneControl, Error, TEXT("'%s' Failed to find an Enhanced Input Component! This template is built to use the Enhanced Input system. If you intend to use the legacy system, then you will need to update this C++ file."), *GetNameSafe(this));
		}

		// Bind camera switch keys (using legacy input for number keys)
		InputComponent->BindAction("SwitchToTopDown", IE_Pressed, this, &AUE5DroneControlPlayerController::SwitchToTopDownCharacter);
		InputComponent->BindAction("SwitchToRealTime", IE_Pressed, this, &AUE5DroneControlPlayerController::SwitchToRealTimeDrone);
		BindSharedCameraInput();
	}
}

void AUE5DroneControlPlayerController::OnInputStarted()
{
	StopMovement();

	// Update the move destination to wherever the cursor is pointing at
	UpdateCachedDestination();
}

void AUE5DroneControlPlayerController::OnSetDestinationTriggered()
{
	// We flag that the input is being pressed
	FollowTime += GetWorld()->GetDeltaSeconds();
	
	// Update the move destination to wherever the cursor is pointing at
	UpdateCachedDestination();
	
	// Move towards mouse pointer or touch
	APawn* ControlledPawn = GetPawn();
	if (!bIsFreeCameraMode && ControlledPawn != nullptr)
	{
		FVector WorldDirection = (CachedDestination - ControlledPawn->GetActorLocation()).GetSafeNormal();
		ControlledPawn->AddMovementInput(WorldDirection, 1.0, false);
	}
}

void AUE5DroneControlPlayerController::OnSetDestinationReleased()
{
	// If it was a short press
	if (!bIsFreeCameraMode && FollowTime <= ShortPressThreshold)
	{
		// We move there and spawn some particles
		UAIBlueprintHelperLibrary::SimpleMoveToLocation(this, CachedDestination);
		UNiagaraFunctionLibrary::SpawnSystemAtLocation(this, FXCursor, CachedDestination, FRotator::ZeroRotator, FVector(1.f, 1.f, 1.f), true, true, ENCPoolMethod::None, true);
	}

	// 每次点击释放都更新目标点并开始持续发送（由角色处理）
	if (AUE5DroneControlCharacter* Drone = Cast<AUE5DroneControlCharacter>(GetPawn()))
	{
		Drone->SetClickTargetLocation(CachedDestination, 1);
	}

	FollowTime = 0.f;
}

// Triggered every frame when the input is held down
void AUE5DroneControlPlayerController::OnTouchTriggered()
{
	bIsTouch = true;
	OnSetDestinationTriggered();
}

void AUE5DroneControlPlayerController::OnTouchReleased()
{
	bIsTouch = false;
	OnSetDestinationReleased();
}

void AUE5DroneControlPlayerController::UpdateCachedDestination()
{
	// We look for the location in the world where the player has pressed the input
	FHitResult Hit;
	bool bHitSuccessful = false;
	if (bIsTouch)
	{
		bHitSuccessful = GetHitResultUnderFinger(ETouchIndex::Touch1, ECollisionChannel::ECC_Visibility, true, Hit);
	}
	else
	{
		bHitSuccessful = GetHitResultUnderCursor(ECollisionChannel::ECC_Visibility, true, Hit);
	}

	// If we hit a surface, cache the location
	if (bHitSuccessful)
	{
		CachedDestination = Hit.Location;
	}
}

// --- 【新增】切换到TopDown角色视角 (数字键0) ---
void AUE5DroneControlPlayerController::SwitchToTopDownCharacter()
{
	// Get the controlled pawn (should be TopDownCharacter)
	APawn* ControlledPawn = GetPawn();
	if (ControlledPawn)
	{
		SetFollowViewTarget(ControlledPawn);
		UE_LOG(LogTemp, Log, TEXT("Camera switched to TopDown Character"));
	}
}

// --- 【新增】切换到RealTimeDrone视角 (数字键1) ---
void AUE5DroneControlPlayerController::SwitchToRealTimeDrone()
{
	// Find RealTimeDrone if not cached
	if (!CachedRealTimeDrone)
	{
		TArray<AActor*> FoundActors;
		UGameplayStatics::GetAllActorsOfClass(GetWorld(), ARealTimeDroneReceiver::StaticClass(), FoundActors);

		if (FoundActors.Num() > 0)
		{
			CachedRealTimeDrone = Cast<ARealTimeDroneReceiver>(FoundActors[0]);
		}
	}

	// Switch to RealTimeDrone camera
	if (CachedRealTimeDrone)
	{
		SetFollowViewTarget(CachedRealTimeDrone);
		UE_LOG(LogTemp, Log, TEXT("Camera switched to RealTime Drone"));
	}
	else
	{
		UE_LOG(LogTemp, Warning, TEXT("RealTimeDrone not found in the level!"));
	}
}

AActor* AUE5DroneControlPlayerController::GetPreferredFollowTarget() const
{
	if (CachedFollowTarget && CachedFollowTarget != FreeCameraPawn)
	{
		return CachedFollowTarget;
	}

	AActor* CurrentViewTarget = GetViewTarget();
	if (CurrentViewTarget && CurrentViewTarget != FreeCameraPawn)
	{
		return CurrentViewTarget;
	}

	return GetPawn();
}

bool AUE5DroneControlPlayerController::SupportsFreeCameraMode() const
{
	return true;
}

void AUE5DroneControlPlayerController::BindSharedCameraInput()
{
	if (!InputComponent)
	{
		return;
	}

	InputComponent->BindAction("ToggleFreeCamera", IE_Pressed, this, &AUE5DroneControlPlayerController::ToggleFreeCameraMode);
	InputComponent->BindAxis("FreeCamForward", this, &AUE5DroneControlPlayerController::MoveFreeCameraForward);
	InputComponent->BindAxis("FreeCamRight", this, &AUE5DroneControlPlayerController::MoveFreeCameraRight);
	InputComponent->BindAxis("FreeCamUp", this, &AUE5DroneControlPlayerController::MoveFreeCameraUp);
	InputComponent->BindAxis("FreeCamTurn", this, &AUE5DroneControlPlayerController::LookFreeCameraYaw);
	InputComponent->BindAxis("FreeCamLookUp", this, &AUE5DroneControlPlayerController::LookFreeCameraPitch);
}

void AUE5DroneControlPlayerController::ToggleFreeCameraMode()
{
	if (!SupportsFreeCameraMode())
	{
		UE_LOG(LogUE5DroneControl, Verbose, TEXT("Free camera mode is not available in the current context"));
		return;
	}

	if (bIsFreeCameraMode)
	{
		ExitFreeCameraMode();
	}
	else
	{
		EnterFreeCameraMode();
	}
}

void AUE5DroneControlPlayerController::MoveFreeCameraForward(float Value)
{
	if (bIsFreeCameraMode && FreeCameraPawn)
	{
		FreeCameraPawn->AddForwardInput(Value);
	}
}

void AUE5DroneControlPlayerController::MoveFreeCameraRight(float Value)
{
	if (bIsFreeCameraMode && FreeCameraPawn)
	{
		FreeCameraPawn->AddRightInput(Value);
	}
}

void AUE5DroneControlPlayerController::MoveFreeCameraUp(float Value)
{
	if (bIsFreeCameraMode && FreeCameraPawn)
	{
		FreeCameraPawn->AddUpInput(Value);
	}
}

void AUE5DroneControlPlayerController::LookFreeCameraYaw(float Value)
{
	if (bIsFreeCameraMode && FreeCameraPawn)
	{
		FreeCameraPawn->AddYawInput(Value);
	}
}

void AUE5DroneControlPlayerController::LookFreeCameraPitch(float Value)
{
	if (bIsFreeCameraMode && FreeCameraPawn)
	{
		FreeCameraPawn->AddPitchInput(Value);
	}
}

void AUE5DroneControlPlayerController::SetFollowViewTarget(AActor* NewTarget, bool bBlendImmediately)
{
	if (!NewTarget || NewTarget == FreeCameraPawn)
	{
		return;
	}

	CachedFollowTarget = NewTarget;

	if (!bIsFreeCameraMode && bBlendImmediately)
	{
		SetViewTargetWithBlend(NewTarget, CameraBlendTime);
	}
}

bool AUE5DroneControlPlayerController::EnsureFreeCameraPawn()
{
	if (FreeCameraPawn)
	{
		return true;
	}

	FActorSpawnParameters SpawnParameters;
	SpawnParameters.Owner = this;
	SpawnParameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

	UClass* PawnClass = FreeCameraPawnClass.Get() ? FreeCameraPawnClass.Get() : ADroneFreeCameraPawn::StaticClass();
	FreeCameraPawn = GetWorld()->SpawnActor<ADroneFreeCameraPawn>(PawnClass, FVector::ZeroVector, FRotator::ZeroRotator, SpawnParameters);

	return FreeCameraPawn != nullptr;
}

void AUE5DroneControlPlayerController::EnterFreeCameraMode()
{
	if (!EnsureFreeCameraPawn() || !PlayerCameraManager)
	{
		return;
	}

	AActor* FollowTarget = ResolveFollowTarget();
	if (FollowTarget)
	{
		CachedFollowTarget = FollowTarget;
	}

	FreeCameraPawn->SnapToCameraTransform(PlayerCameraManager->GetCameraLocation(), PlayerCameraManager->GetCameraRotation());
	SetViewTargetWithBlend(FreeCameraPawn, CameraBlendTime);
	bIsFreeCameraMode = true;
}

void AUE5DroneControlPlayerController::ExitFreeCameraMode()
{
	bIsFreeCameraMode = false;

	if (AActor* FollowTarget = ResolveFollowTarget())
	{
		SetViewTargetWithBlend(FollowTarget, CameraBlendTime);
	}
}

AActor* AUE5DroneControlPlayerController::ResolveFollowTarget() const
{
	if (AActor* PreferredTarget = GetPreferredFollowTarget())
	{
		return PreferredTarget;
	}

	if (CachedFollowTarget && CachedFollowTarget != FreeCameraPawn)
	{
		return CachedFollowTarget;
	}

	return GetPawn();
}
