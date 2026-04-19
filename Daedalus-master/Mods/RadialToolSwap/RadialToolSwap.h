#pragma once
#include "Mod/Mod.h"
#include <vector>
#include <string>

struct ToolEntry
{
	std::string RowName;
	std::string DisplayName;
	int32_t SlotIndex;
};

class RadialToolSwap : public Mod
{
public:
	RadialToolSwap()
	{
		ModName = "RadialToolSwap";
		ModVersion = "1.0.0";
		ModDescription = "Radial wheel for quick-swapping tools between backpack and quickbar";
		ModAuthors = "Ludus/AgentKush";
		ModLoaderVersion = "2.3.0";
		ModRef = this;
		CompleteModCreation();
	}

	virtual void InitializeMod() override;
	virtual void InitGameState() override;
	virtual void BeginPlay(UE4::AActor* Actor) override;
	virtual void PostBeginPlay(std::wstring ModActorName, UE4::AActor* Actor) override;
	virtual void DX11Present(ID3D11Device* pDevice, ID3D11DeviceContext* pContext, ID3D11RenderTargetView* pRenderTargetView) override;
	virtual void DX12Present(IDXGISwapChain* pSwapChain, ID3D12GraphicsCommandList* pCommandList) override;
	virtual void OnModMenuButtonPressed() override;
	virtual void DrawImGui() override;

private:
	bool bWheelOpen = false;
	bool bWasKeyDown = false;
	int32_t HoveredIndex = -1;
	float WheelRadius = 180.0f;
	float DeadZone = 35.0f;
	int WheelKey = VK_MBUTTON;

	std::vector<ToolEntry> BackpackTools;
	static constexpr int MAX_WHEEL_SLOTS = 8;

	UE4::AActor* CachedPlayerPawn = nullptr;

	static constexpr uintptr_t OFF_BackpackInventory = 0xb30;
	static constexpr uintptr_t OFF_QuickbarInventory = 0xb38;
	static constexpr uintptr_t OFF_FocusedQuickbarSlot = 0xb50;
	static constexpr uintptr_t OFF_Inv_Slots = 0xe8;
	static constexpr uintptr_t OFF_FastArray_TArray = 0x108;
	static constexpr uintptr_t OFF_Slot_RowName = 0x30;
	static constexpr size_t SIZEOF_FInventorySlot = 0x100;

	UE4::AActor* GetPlayerPawn();
	UE4::UObject* GetBackpackInventory();
	UE4::UObject* GetQuickbarInventory();
	int32_t GetFocusedQuickbarSlot();

	void ScanBackpackForTools();
	bool IsToolItem(const std::string& rowName) const;
	std::string CleanDisplayName(const std::string& rowName) const;

	void ExecuteSwap(int32_t toolIndex);
	void DrawRadialWheel();
};