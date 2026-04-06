// Copyright Epic Games, Inc. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
//#include "Templates/SubclassOf.h"
#include "GameFramework/PlayerController.h"
#include "UE5DroneControlPlayerController.generated.h"

class UNiagaraSystem;
class UInputMappingContext;
class UInputAction;
class UPathFollowingComponent;
class ADroneFreeCameraPawn;

DECLARE_LOG_CATEGORY_EXTERN(LogTemplateCharacter, Log, All);

/**
 *  Player controller for a top-down perspective game.
 *  Implements point and click based controls
 */
UCLASS(abstract)
class AUE5DroneControlPlayerController : public APlayerController
{
	GENERATED_BODY()

protected:

	/** Component used for moving along a NavMesh path. */
	UPROPERTY(VisibleDefaultsOnly, Category = AI)
	TObjectPtr<UPathFollowingComponent> PathFollowingComponent;

	/** Time Threshold to know if it was a short press */
	UPROPERTY(EditAnywhere, Category="Input")
	float ShortPressThreshold;

	/** FX Class that we will spawn when clicking */
	UPROPERTY(EditAnywhere, Category="Input")
	TObjectPtr<UNiagaraSystem> FXCursor;

	/** MappingContext */
	UPROPERTY(EditAnywhere, Category="Input")
	TObjectPtr<UInputMappingContext> DefaultMappingContext;
	
	/** Jump Input Action */
	UPROPERTY(EditAnywhere, Category="Input")
	TObjectPtr<UInputAction> SetDestinationClickAction;

	/** Jump Input Action */
	UPROPERTY(EditAnywhere, Category="Input")
	TObjectPtr<UInputAction> SetDestinationTouchAction;

	/** True if the controlled character should navigate to the mouse cursor. */
	uint32 bMoveToMouseCursor : 1;

	/** Set to true if we're using touch input */
	uint32 bIsTouch : 1;

	/** Saved location of the character movement destination */
	FVector CachedDestination;

	/** Time that the click input has been pressed */
	float FollowTime = 0.0f;

public:

	/** Constructor */
	AUE5DroneControlPlayerController();

	UFUNCTION(BlueprintPure, Category = "Camera")
	bool IsInFreeCameraMode() const { return bIsFreeCameraMode; }

protected:

	/** Initialize input bindings */
	virtual void SetupInputComponent() override;

	
	/** Input handlers */
	void OnInputStarted();
	void OnSetDestinationTriggered();
	void OnSetDestinationReleased();
	void OnTouchTriggered();
	void OnTouchReleased();

	/** Helper function to get the move destination */
	void UpdateCachedDestination();

	// --- 【新增】视角切换函数 ---
	/** Switch camera to TopDown character (key 0) */
	virtual void SwitchToTopDownCharacter();

	/** Switch camera to RealTime drone (key 1) */
	void SwitchToRealTimeDrone();

	virtual AActor* GetPreferredFollowTarget() const;
	virtual bool SupportsFreeCameraMode() const;

	void BindSharedCameraInput();
	void ToggleFreeCameraMode();
	void MoveFreeCameraForward(float Value);
	void MoveFreeCameraRight(float Value);
	void MoveFreeCameraUp(float Value);
	void LookFreeCameraYaw(float Value);
	void LookFreeCameraPitch(float Value);
	void SetFollowViewTarget(AActor* NewTarget, bool bBlendImmediately = true);

	UPROPERTY(EditAnywhere, Category = "Camera")
	TSubclassOf<ADroneFreeCameraPawn> FreeCameraPawnClass;

	UPROPERTY(EditAnywhere, Category = "Camera", meta = (ClampMin = "0.0"))
	float CameraBlendTime = 0.35f;

private:
	/** Reference to the RealTime drone actor */
	UPROPERTY()
	class ARealTimeDroneReceiver* CachedRealTimeDrone;

	UPROPERTY()
	TObjectPtr<ADroneFreeCameraPawn> FreeCameraPawn;

	UPROPERTY()
	TObjectPtr<AActor> CachedFollowTarget;

	bool bIsFreeCameraMode = false;

	bool EnsureFreeCameraPawn();
	void EnterFreeCameraMode();
	void ExitFreeCameraMode();
	AActor* ResolveFollowTarget() const;

	// --- 点击目标点持续发送（由角色处理） ---
};
