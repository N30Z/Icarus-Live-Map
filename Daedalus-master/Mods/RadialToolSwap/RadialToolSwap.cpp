#include "RadialToolSwap.h"
#include "Utilities/MinHook.h"
#include <SDK.hpp>
#include <sdk.h>
#include <cmath>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

void RadialToolSwap::InitializeMod()
{
	UE4::InitSDK();
	SetupHooks();
	UseMenuButton = true;
	Log::Print("[RadialToolSwap] Mod initialized - Hold Middle Mouse to open radial wheel");
}

void RadialToolSwap::InitGameState() {}
void RadialToolSwap::BeginPlay(UE4::AActor* Actor) {}
void RadialToolSwap::PostBeginPlay(std::wstring ModActorName, UE4::AActor* Actor) {}
void RadialToolSwap::DX11Present(ID3D11Device* pDevice, ID3D11DeviceContext* pContext, ID3D11RenderTargetView* pRenderTargetView) {}
void RadialToolSwap::DX12Present(IDXGISwapChain* pSwapChain, ID3D12GraphicsCommandList* pCommandList) {}

void RadialToolSwap::OnModMenuButtonPressed()
{
	ScanBackpackForTools();
	Log::Print("[RadialToolSwap] Rescanned backpack - found %d tools", (int)BackpackTools.size());

	auto pawn = GetPlayerPawn();
	if (!pawn) { Log::Print("[RadialToolSwap] No pawn for enumeration"); return; }

	Log::Print("[RadialToolSwap] === ENUMERATING FUNCTIONS ON PLAYER PAWN ===");
	Log::Print("[RadialToolSwap] Pawn: %s", pawn->GetFullName().c_str());

	auto funcClass = UE4::UObject::FindClass("Class CoreUObject.Function");
	if (!funcClass) { Log::Print("[RadialToolSwap] Cannot find UFunction class"); return; }

	UE4::UClass* cls = pawn->GetClass();
	int depth = 0;
	while (cls && depth < 15)
	{
		Log::Print("[RadialToolSwap] --- Class: %s ---", cls->GetName().c_str());
		if (UE4::UObject::IsChunkedArray())
		{
			for (int i = 0; i < UE4::UObject::GObjects->GetAsChunckArray().Num(); ++i)
			{
				auto obj = UE4::UObject::GObjects->GetAsChunckArray().GetByIndex(i).Object;
				if (!obj || !obj->IsA(funcClass) || obj->GetOuter() != (UE4::UObject*)cls) continue;
				std::string fn = obj->GetName();
				if (fn.find("Swap") != std::string::npos || fn.find("Move") != std::string::npos ||
					fn.find("Inventory") != std::string::npos || fn.find("Item") != std::string::npos ||
					fn.find("Quickbar") != std::string::npos || fn.find("Slot") != std::string::npos ||
					fn.find("Equip") != std::string::npos || fn.find("Transfer") != std::string::npos ||
					fn.find("Server_") != std::string::npos || fn.find("Backpack") != std::string::npos ||
					fn.find("Tool") != std::string::npos)
					Log::Print("[RadialToolSwap]   PAWN: %s", fn.c_str());
			}
		}
		else
		{
			for (int i = 0; i < UE4::UObject::GObjects->GetAsTUArray().Num(); ++i)
			{
				auto obj = UE4::UObject::GObjects->GetAsTUArray().GetByIndex(i).Object;
				if (!obj || !obj->IsA(funcClass) || obj->GetOuter() != (UE4::UObject*)cls) continue;
				std::string fn = obj->GetName();
				if (fn.find("Swap") != std::string::npos || fn.find("Move") != std::string::npos ||
					fn.find("Inventory") != std::string::npos || fn.find("Item") != std::string::npos ||
					fn.find("Quickbar") != std::string::npos || fn.find("Slot") != std::string::npos ||
					fn.find("Equip") != std::string::npos || fn.find("Transfer") != std::string::npos ||
					fn.find("Server_") != std::string::npos || fn.find("Backpack") != std::string::npos ||
					fn.find("Tool") != std::string::npos)
					Log::Print("[RadialToolSwap]   PAWN: %s", fn.c_str());
			}
		}
		cls = static_cast<UE4::UClass*>(cls->GetSuperField());
		depth++;
	}

	auto backpack = GetBackpackInventory();
	if (backpack)
	{
		Log::Print("[RadialToolSwap] === INVENTORY FUNCTIONS ===");
		Log::Print("[RadialToolSwap] Backpack: %s", backpack->GetFullName().c_str());
		UE4::UClass* ic = backpack->GetClass();
		int id = 0;
		while (ic && id < 10)
		{
			Log::Print("[RadialToolSwap] --- Inv Class: %s ---", ic->GetName().c_str());
			if (UE4::UObject::IsChunkedArray())
			{
				for (int i = 0; i < UE4::UObject::GObjects->GetAsChunckArray().Num(); ++i)
				{
					auto obj = UE4::UObject::GObjects->GetAsChunckArray().GetByIndex(i).Object;
					if (!obj || !obj->IsA(funcClass) || obj->GetOuter() != (UE4::UObject*)ic) continue;
					std::string fn = obj->GetName();
					if (fn.find("Swap") != std::string::npos || fn.find("Move") != std::string::npos ||
						fn.find("Transfer") != std::string::npos || fn.find("Item") != std::string::npos ||
						fn.find("Slot") != std::string::npos || fn.find("Add") != std::string::npos ||
						fn.find("Remove") != std::string::npos || fn.find("Server") != std::string::npos)
						Log::Print("[RadialToolSwap]   INV: %s", fn.c_str());
				}
			}
			else
			{
				for (int i = 0; i < UE4::UObject::GObjects->GetAsTUArray().Num(); ++i)
				{
					auto obj = UE4::UObject::GObjects->GetAsTUArray().GetByIndex(i).Object;
					if (!obj || !obj->IsA(funcClass) || obj->GetOuter() != (UE4::UObject*)ic) continue;
					std::string fn = obj->GetName();
					if (fn.find("Swap") != std::string::npos || fn.find("Move") != std::string::npos ||
						fn.find("Transfer") != std::string::npos || fn.find("Item") != std::string::npos ||
						fn.find("Slot") != std::string::npos || fn.find("Add") != std::string::npos ||
						fn.find("Remove") != std::string::npos || fn.find("Server") != std::string::npos)
						Log::Print("[RadialToolSwap]   INV: %s", fn.c_str());
				}
			}
			ic = static_cast<UE4::UClass*>(ic->GetSuperField());
			id++;
		}
		Log::Print("[RadialToolSwap] === ENUMERATION COMPLETE ===");
	}
}

UE4::AActor* RadialToolSwap::GetPlayerPawn()
{
	if (CachedPlayerPawn) return CachedPlayerPawn;

	static auto fn = UE4::UObject::FindObject<UE4::UFunction>("Function Engine.GameplayStatics.GetPlayerPawn");
	auto statics = UE4::UObject::FindObject<UE4::UGameplayStatics>("Class Engine.GameplayStatics");
	if (!fn || !statics) return nullptr;

	struct { UE4::UObject* WorldContextObject; int PlayerIndex; UE4::AActor* ReturnValue; } params;
	params.WorldContextObject = UE4::UWorld::GetWorld();
	params.PlayerIndex = 0;
	params.ReturnValue = nullptr;
	statics->ProcessEvent(fn, &params);
	CachedPlayerPawn = params.ReturnValue;
	return CachedPlayerPawn;
}

UE4::UObject* RadialToolSwap::GetBackpackInventory()
{
	auto pawn = GetPlayerPawn();
	if (!pawn) return nullptr;
	return *(UE4::UObject**)((uintptr_t)pawn + OFF_BackpackInventory);
}

UE4::UObject* RadialToolSwap::GetQuickbarInventory()
{
	auto pawn = GetPlayerPawn();
	if (!pawn) return nullptr;
	return *(UE4::UObject**)((uintptr_t)pawn + OFF_QuickbarInventory);
}

int32_t RadialToolSwap::GetFocusedQuickbarSlot()
{
	auto pawn = GetPlayerPawn();
	if (!pawn) return 0;
	return *(int32_t*)((uintptr_t)pawn + OFF_FocusedQuickbarSlot);
}

bool RadialToolSwap::IsToolItem(const std::string& rowName) const
{
	static const char* toolPrefixes[] = {
		"Pickaxe_", "Axe_", "Knife_", "Sword_", "Spear_",
		"Bow_", "Crossbow_", "Rifle_", "Pistol_", "Shotgun_",
		"Hammer_", "Sickle_", "Torch_", "Flashlight_", "Radar_",
		"FishingRod_", "Larkwell_", "RepairHammer_", "Lance_",
		"MiningDrill_", "ChainSaw_", "Shears_"
	};
	for (const auto& prefix : toolPrefixes)
		if (rowName.find(prefix) == 0) return true;
	return false;
}

std::string RadialToolSwap::CleanDisplayName(const std::string& rowName) const
{
	std::string name = rowName;
	for (auto& c : name) if (c == '_') c = ' ';
	return name;
}

void RadialToolSwap::ScanBackpackForTools()
{
	BackpackTools.clear();
	auto backpack = GetBackpackInventory();
	if (!backpack) { Log::Print("[RadialToolSwap] No backpack inventory found"); return; }

	uintptr_t slotsBase = (uintptr_t)backpack + OFF_Inv_Slots;
	uintptr_t arrayAddr = slotsBase + OFF_FastArray_TArray;
	uintptr_t dataPtr = *(uintptr_t*)arrayAddr;
	int32_t count = *(int32_t*)(arrayAddr + 0x08);

	if (!dataPtr || count <= 0 || count > 100) {
		Log::Print("[RadialToolSwap] Invalid slot array: data=%p count=%d", (void*)dataPtr, count);
		return;
	}

	for (int32_t i = 0; i < count; ++i) {
		uintptr_t slotAddr = dataPtr + (i * SIZEOF_FInventorySlot);
		UE4::FName* rowNamePtr = (UE4::FName*)(slotAddr + OFF_Slot_RowName);
		if (!rowNamePtr || rowNamePtr->ComparisonIndex == 0) continue;

		std::string rowName;
		try { rowName = rowNamePtr->GetName(); } catch (...) { continue; }
		if (rowName.empty() || rowName == "None") continue;

		if (IsToolItem(rowName)) {
			ToolEntry entry;
			entry.RowName = rowName;
			entry.DisplayName = CleanDisplayName(rowName);
			entry.SlotIndex = i;
			BackpackTools.push_back(entry);
			if ((int)BackpackTools.size() >= MAX_WHEEL_SLOTS) break;
		}
	}
	Log::Print("[RadialToolSwap] Found %d tools in backpack", (int)BackpackTools.size());
}

void RadialToolSwap::ExecuteSwap(int32_t toolIndex)
{
	if (toolIndex < 0 || toolIndex >= (int32_t)BackpackTools.size()) return;

	auto backpack = GetBackpackInventory();
	auto quickbar = GetQuickbarInventory();
	if (!backpack || !quickbar) { Log::Print("[RadialToolSwap] Missing inventory for swap"); return; }

	int32_t backpackSlot = BackpackTools[toolIndex].SlotIndex;
	int32_t quickbarSlot = GetFocusedQuickbarSlot();

	Log::Print("[RadialToolSwap] Swapping: backpack[%d] (%s) <-> quickbar[%d]",
		backpackSlot, BackpackTools[toolIndex].RowName.c_str(), quickbarSlot);

	auto pawn = GetPlayerPawn();
	if (!pawn) return;

	UE4::UFunction* swapFunc = nullptr;
	static const char* swapFuncNames[] = {
		"Server_SwapInventoryItems", "ServerSwapInventoryItems",
		"Server_RequestSwapItems", "SwapInventoryItems",
		"Server_MoveInventoryItem", "ServerMoveInventoryItem",
		"SwapItems", "MoveItem", "TransferItem", nullptr
	};

	for (int i = 0; swapFuncNames[i] != nullptr; ++i) {
		if (pawn->DoesObjectContainFunction(swapFuncNames[i])) {
			swapFunc = pawn->GetFunction(swapFuncNames[i]);
			if (swapFunc) {
				Log::Print("[RadialToolSwap] Found swap function: %s", swapFuncNames[i]);
				break;
			}
		}
	}

	if (swapFunc) {
		struct { UE4::UObject* SourceInventory; int32_t SourceSlot; UE4::UObject* DestInventory; int32_t DestSlot; } swapParams;
		swapParams.SourceInventory = backpack;
		swapParams.SourceSlot = backpackSlot;
		swapParams.DestInventory = quickbar;
		swapParams.DestSlot = quickbarSlot;
		pawn->ProcessEvent(swapFunc, &swapParams);
		Log::Print("[RadialToolSwap] Swap executed via ProcessEvent");
	} else {
		static const char* invSwapNames[] = { "SwapItems", "MoveItem", "TransferItem", nullptr };
		for (int i = 0; invSwapNames[i] != nullptr; ++i) {
			if (backpack->DoesObjectContainFunction(invSwapNames[i])) {
				auto func = backpack->GetFunction(invSwapNames[i]);
				if (func) {
					Log::Print("[RadialToolSwap] Found inventory function: %s", invSwapNames[i]);
					struct { int32_t FromSlot; UE4::UObject* ToInventory; int32_t ToSlot; bool ReturnValue; } moveParams;
					moveParams.FromSlot = backpackSlot;
					moveParams.ToInventory = quickbar;
					moveParams.ToSlot = quickbarSlot;
					moveParams.ReturnValue = false;
					backpack->ProcessEvent(func, &moveParams);
					Log::Print("[RadialToolSwap] Swap executed via inventory ProcessEvent");
					break;
				}
			}
		}
	}
	ScanBackpackForTools();
}

void RadialToolSwap::DrawRadialWheel()
{
	ImGuiIO& io = ImGui::GetIO();
	ImDrawList* drawList = ImGui::GetForegroundDrawList();
	float centerX = io.DisplaySize.x * 0.5f;
	float centerY = io.DisplaySize.y * 0.5f;
	int numSlots = (int)BackpackTools.size();
	if (numSlots == 0) numSlots = 1;
	float segmentAngle = (2.0f * (float)M_PI) / (float)numSlots;

	drawList->AddRectFilled(ImVec2(0, 0), ImVec2(io.DisplaySize.x, io.DisplaySize.y), IM_COL32(0, 0, 0, 100));
	drawList->AddCircleFilled(ImVec2(centerX, centerY), WheelRadius + 20.0f, IM_COL32(15, 15, 20, 200), 64);
	drawList->AddCircle(ImVec2(centerX, centerY), WheelRadius + 20.0f, IM_COL32(80, 140, 220, 180), 64, 2.0f);
	drawList->AddCircleFilled(ImVec2(centerX, centerY), DeadZone, IM_COL32(25, 25, 35, 220), 32);
	drawList->AddCircle(ImVec2(centerX, centerY), DeadZone, IM_COL32(60, 100, 180, 150), 32, 1.5f);

	float dx = io.MousePos.x - centerX, dy = io.MousePos.y - centerY;
	float dist = sqrtf(dx * dx + dy * dy);
	HoveredIndex = -1;
	if (dist > DeadZone && BackpackTools.size() > 0) {
		float angle = atan2f(dy, dx);
		if (angle < 0) angle += 2.0f * (float)M_PI;
		float offsetAngle = angle + (float)M_PI * 0.5f;
		if (offsetAngle >= 2.0f * (float)M_PI) offsetAngle -= 2.0f * (float)M_PI;
		float adjusted = offsetAngle + segmentAngle * 0.5f;
		if (adjusted >= 2.0f * (float)M_PI) adjusted -= 2.0f * (float)M_PI;
		int seg = (int)(adjusted / segmentAngle);
		if (seg >= 0 && seg < (int)BackpackTools.size()) HoveredIndex = seg;
	}

	for (int i = 0; i < numSlots; ++i) {
		float lineAngle = -((float)M_PI * 0.5f) + (segmentAngle * i);
		drawList->AddLine(
			ImVec2(centerX + cosf(lineAngle) * DeadZone, centerY + sinf(lineAngle) * DeadZone),
			ImVec2(centerX + cosf(lineAngle) * (WheelRadius + 15.0f), centerY + sinf(lineAngle) * (WheelRadius + 15.0f)),
			IM_COL32(60, 100, 180, 120), 1.0f);
	}

	for (int i = 0; i < (int)BackpackTools.size(); ++i) {
		float midAngle = -((float)M_PI * 0.5f) + (segmentAngle * (i + 0.5f));
		if (i == HoveredIndex) {
			float startAngle = -((float)M_PI * 0.5f) + (segmentAngle * i);
			ImVector<ImVec2> points;
			for (int s = 0; s <= 20; ++s) {
				float a = startAngle + (segmentAngle * s / 20);
				points.push_back(ImVec2(centerX + cosf(a) * (DeadZone + 2.0f), centerY + sinf(a) * (DeadZone + 2.0f)));
			}
			for (int s = 20; s >= 0; --s) {
				float a = startAngle + (segmentAngle * s / 20);
				points.push_back(ImVec2(centerX + cosf(a) * (WheelRadius + 15.0f), centerY + sinf(a) * (WheelRadius + 15.0f)));
			}
			drawList->AddConvexPolyFilled(points.Data, points.Size, IM_COL32(50, 120, 255, 100));
		}
		float labelRadius = (DeadZone + WheelRadius + 15.0f) * 0.55f;
		float labelX = centerX + cosf(midAngle) * labelRadius;
		float labelY = centerY + sinf(midAngle) * labelRadius;
		const char* name = BackpackTools[i].DisplayName.c_str();
		ImVec2 textSize = ImGui::CalcTextSize(name);
		ImU32 textCol = (i == HoveredIndex) ? IM_COL32(255, 255, 255, 255) : IM_COL32(180, 200, 220, 220);
		drawList->AddText(ImVec2(labelX - textSize.x * 0.5f, labelY - textSize.y * 0.5f), textCol, name);
	}

	const char* centerText = "Select Tool";
	ImU32 centerTextCol = IM_COL32(150, 170, 200, 200);
	if (HoveredIndex >= 0 && HoveredIndex < (int)BackpackTools.size()) {
		centerText = BackpackTools[HoveredIndex].DisplayName.c_str();
		centerTextCol = IM_COL32(100, 200, 255, 255);
	} else if (BackpackTools.empty()) {
		centerText = "No Tools Found";
		centerTextCol = IM_COL32(255, 100, 100, 200);
	}
	ImVec2 ctSize = ImGui::CalcTextSize(centerText);
	drawList->AddText(ImVec2(centerX - ctSize.x * 0.5f, centerY - ctSize.y * 0.5f), centerTextCol, centerText);
}

void RadialToolSwap::DrawImGui()
{
	bool keyDown = (GetAsyncKeyState(WheelKey) & 0x8000) != 0;
	if (keyDown && !bWasKeyDown) {
		bWheelOpen = true;
		HoveredIndex = -1;
		CachedPlayerPawn = nullptr;
		ScanBackpackForTools();
	}
	if (!keyDown && bWasKeyDown && bWheelOpen) {
		if (HoveredIndex >= 0) ExecuteSwap(HoveredIndex);
		bWheelOpen = false;
		HoveredIndex = -1;
	}
	bWasKeyDown = keyDown;
	if (bWheelOpen) DrawRadialWheel();
}