#pragma once
#include <string>
#include <vector>
#include "Utilities/Logger.h"
#include "GameInfo/GameInfo.h"
#include "Utilities/Globals.h"
#include "../LoaderUI.h"
#include "Ue4.hpp"

class LOADER_API Mod
{
public:
	// Mod Default Variables
	std::string ModName;
	std::string ModVersion;
	std::string ModDescription;
	std::string ModAuthors;
	std::string ModLoaderVersion;
	bool UseMenuButton = 0;

	//ModInternals
	bool IsFinishedCreating = 0;
	
	// Dependency system - set these in your mod constructor
	// Each string is the ModName of a required dependency
	// NOTE: This field MUST stay after IsFinishedCreating to preserve ABI compatibility
	// with mods compiled against older Mod.h layouts
	std::vector<std::string> Dependencies;
	
	//Used Internally to setup Hook Event System
	void SetupHooks();

	//Called after each mod is injected, Looped through via gloabals
	virtual void InitializeMod();

	//InitGameState Call
	virtual void InitGameState();

	//Call ImGui Here
	virtual void DrawImGui();

	//Beginplay Hook of Every Actor
	virtual void BeginPlay(UE4::AActor* Actor);

	//PostBeginPlay of EVERY Blueprint ModActor
	virtual void PostBeginPlay(std::wstring ModActorName, UE4::AActor* Actor);

	//DX11 hook for when an image will be presented to the screen
	virtual void DX11Present(ID3D11Device* pDevice, ID3D11DeviceContext* pContext, ID3D11RenderTargetView* pRenderTargetView);

	//DX12 hook for when an image will be presented to the screen
	virtual void DX12Present(IDXGISwapChain* pSwapChain, ID3D12GraphicsCommandList* pCommandList);

	virtual void OnModMenuButtonPressed();

	//Called When Mod Construct Finishes
	void CompleteModCreation();

	static Mod* ModRef;
};