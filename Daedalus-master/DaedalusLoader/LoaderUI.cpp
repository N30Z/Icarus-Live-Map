#include "LoaderUI.h"
#include "DaedalusLoader/Mod/Mod.h"
#include "DaedalusLoader/CrashHandler/CrashHandler.h"
#include "Utilities/Logger.h"
#include "Memory/mem.h"
#include "Utilities/Dumper.h"
#include "Utilities/Globals.h"
#include "Utilities/MinHook.h"
#include "Utilities/Version.h"

LoaderUI* LoaderUI::UI;

// Forward declarations
static void ApplyImGuiStyle();
void DrawImGui();

// Scale ImGui for the current resolution (call after CreateContext, before backend init)
static float g_UIScale = 1.0f;
static void ApplyImGuiScaling(IDXGISwapChain* pSwapChain)
{
	DXGI_SWAP_CHAIN_DESC desc;
	if (SUCCEEDED(pSwapChain->GetDesc(&desc)))
	{
		float height = (float)desc.BufferDesc.Height;
		// Scale relative to 1080p — at 1080p scale=1.0, at 1440p ~1.33, at 4K ~2.0
		g_UIScale = height / 1080.0f;
		if (g_UIScale < 1.0f) g_UIScale = 1.0f;

		ImGuiIO& io = ImGui::GetIO();
		io.FontGlobalScale = g_UIScale;

		Log::Info("ImGui: Scaling UI at %.2f (resolution: %ux%u)", g_UIScale,
			desc.BufferDesc.Width, desc.BufferDesc.Height);
	}
}

LoaderUI* LoaderUI::GetUI()
{
	if (!UI)
	{
		UI = new LoaderUI();
	}
	return UI;
}

namespace TickVars
{
	bool f1_pressed;
	bool bDumpObjects;
	bool bDumpEngineInfo;
	bool bDumpWorldActors;
	bool bExecuteConsoleCommand;
	std::wstring CurrentCommand;
};

namespace UITools
{
	void ObjectDump()
	{
		TickVars::bDumpObjects = true;
	}

	void EngineDump()
	{
		TickVars::bDumpEngineInfo = true;
	}

	void WorldDump()
	{
		TickVars::bDumpWorldActors = true;
	}

	void ExecuteCommand(std::wstring command)
	{
		TickVars::CurrentCommand = command;
		TickVars::bExecuteConsoleCommand = true;
	}
};

void UILogicTick()
{
	while (true)
	{
		int menuKey = DaedalusConfig::GetConfig()->GetMenuKey();
		if (GetAsyncKeyState(menuKey) != 0)
			TickVars::f1_pressed = true;
		else if (TickVars::f1_pressed)
		{
			TickVars::f1_pressed = false;
			if (Global::GetGlobals()->bIsMenuOpen)
			{
				Global::GetGlobals()->bIsMenuOpen = false;
			}
			else
			{
				if (!LoaderUI::GetUI()->IsDXHooked)
				{
					LoaderUI::HookDX();
				}
				Global::GetGlobals()->bIsMenuOpen = true;
			}
		}

		if (TickVars::bDumpObjects)
		{
			TickVars::bDumpObjects = false;
			Dumper::GetDumper()->DumpObjectArray();
		}

		if (TickVars::bDumpEngineInfo)
		{
			TickVars::bDumpEngineInfo = false;
			Dumper::GetDumper()->DumpEngineInfo();
		}

		if (TickVars::bDumpWorldActors)
		{
			TickVars::bDumpWorldActors = false;
			Dumper::GetDumper()->DumpWorldActors();
		}

		if (TickVars::bExecuteConsoleCommand)
		{
			TickVars::bExecuteConsoleCommand = false;
			UE4::UGameplayStatics::ExecuteConsoleCommand(TickVars::CurrentCommand.c_str(), nullptr);
			TickVars::CurrentCommand = L"";
		}
		Sleep(1000 / 60);
	}
}

HRESULT LoaderUI::LoaderResizeBuffers(IDXGISwapChain* pSwapChain, UINT BufferCount, UINT Width, UINT Height, DXGI_FORMAT NewFormat, UINT SwapChainFlags)
{
	if (!LoaderUI::GetUI()->initRendering)
	{
		if (LoaderUI::GetUI()->ActiveRenderer == EDetectedRenderer::DX12)
		{
			// DX12 resize path
			WaitForLastSubmittedFrame();
			ImGui_ImplDX12_InvalidateDeviceObjects();

			// Release back buffer references
			DX12ResizeCleanup();

			HRESULT hr = ResizeBuffers(pSwapChain, BufferCount, Width, Height, NewFormat, SwapChainFlags);

			// Re-acquire back buffers
			IDXGISwapChain3* pSwapChain3 = nullptr;
			if (SUCCEEDED(pSwapChain->QueryInterface(IID_PPV_ARGS(&pSwapChain3))))
			{
				for (int i = 0; i < DX12_NUM_BACK_BUFFERS; i++)
				{
					pSwapChain3->GetBuffer(i, IID_PPV_ARGS(&LoaderUI::GetUI()->pD3D12BackBuffers[i]));
					LoaderUI::GetUI()->pD3D12Device->CreateRenderTargetView(
						LoaderUI::GetUI()->pD3D12BackBuffers[i], NULL, LoaderUI::GetUI()->d3d12RtvHandles[i]);
				}
				pSwapChain3->Release();
			}

			ImGui_ImplDX12_CreateDeviceObjects();
			Log::Info("DX12: ResizeBuffers handled (%ux%u)", Width, Height);
			return hr;
		}
		else
		{
			// DX11 resize path
			if (LoaderUI::GetUI()->pRenderTargetView) {
				LoaderUI::GetUI()->pContext->OMSetRenderTargets(0, 0, 0);
				LoaderUI::GetUI()->pRenderTargetView->Release();
			}

			HRESULT hr = ResizeBuffers(pSwapChain, BufferCount, Width, Height, NewFormat, SwapChainFlags);

			ID3D11Texture2D* pBuffer;
			pSwapChain->GetBuffer(0, __uuidof(ID3D11Texture2D), (void**)&pBuffer);

			LoaderUI::GetUI()->pDevice->CreateRenderTargetView(pBuffer, NULL, &LoaderUI::GetUI()->pRenderTargetView);
			pBuffer->Release();

			LoaderUI::GetUI()->pContext->OMSetRenderTargets(1, &LoaderUI::GetUI()->pRenderTargetView, NULL);

			D3D11_VIEWPORT vp;
			vp.Width = (FLOAT)Width;
			vp.Height = (FLOAT)Height;
			vp.MinDepth = 0.0f;
			vp.MaxDepth = 1.0f;
			vp.TopLeftX = 0;
			vp.TopLeftY = 0;
			LoaderUI::GetUI()->pContext->RSSetViewports(1, &vp);
			return hr;
		}
	}
	else
	{
		HRESULT hr = ResizeBuffers(pSwapChain, BufferCount, Width, Height, NewFormat, SwapChainFlags);
		return hr;
	}
}

void ShowLogicMods()
{
	if (!ImGui::CollapsingHeader("Pak Mods"))
		return;

	for (size_t i = 0; i < Global::GetGlobals()->ModInfoList.size(); i++)
	{
		std::string str(Global::GetGlobals()->ModInfoList[i].ModName.begin(), Global::GetGlobals()->ModInfoList[i].ModName.end());
		std::string ModLabel = str + "##" + std::to_string(i);
		if (ImGui::TreeNode(ModLabel.c_str()))
		{
			std::string Author = "Created By: " + Global::GetGlobals()->ModInfoList[i].ModAuthor;
			ImGui::Text(Author.c_str());
			ImGui::Separator();
			std::string Description = "Description: " + Global::GetGlobals()->ModInfoList[i].ModDescription;
			ImGui::Text(Description.c_str());
			ImGui::Separator();
			std::string Version = "Version: " + Global::GetGlobals()->ModInfoList[i].ModVersion;
			ImGui::Text(Version.c_str());
			ImGui::Separator();
			if (ImGui::TreeNode("Mod Buttons"))
			{
				if (Global::GetGlobals()->ModInfoList[i].IsEnabled && Global::GetGlobals()->ModInfoList[i].CurrentModActor && Global::GetGlobals()->ModInfoList[i].ContainsButton)
				{
					for (size_t bi = 0; bi < Global::GetGlobals()->ModInfoList[i].ModButtons.size(); bi++)
					{
						auto currentmodbutton = Global::GetGlobals()->ModInfoList[i].ModButtons[bi];
						std::string ButtonLabel = currentmodbutton + "##" + std::to_string(i + bi);
						if (ImGui::Button(ButtonLabel.c_str()))
						{
							std::wstring FuncNameAndArgs = L"ModMenuButtonPressed " + std::to_wstring(bi);
							Global::GetGlobals()->ModInfoList[i].CurrentModActor->CallFunctionByNameWithArguments(FuncNameAndArgs.c_str(), nullptr, NULL, true);
						}
					}
					ImGui::Separator();
				}
				ImGui::TreePop();
			}
			std::string ActiveLabel = "Enable##" + std::to_string(i);
			ImGui::Checkbox(ActiveLabel.c_str(), &Global::GetGlobals()->ModInfoList[i].IsEnabled);
			ImGui::TreePop();
		}
	}
}

// Safe pointer probe — must be a pure C function (no C++ objects) for SEH
static bool IsPointerReadable(const void* ptr, size_t size)
{
	__try
	{
		volatile char test = *((const char*)ptr);
		volatile char test2 = *((const char*)ptr + size - 1);
		(void)test;
		(void)test2;
		return true;
	}
	__except (EXCEPTION_EXECUTE_HANDLER)
	{
		return false;
	}
}

// SEH-safe wrapper around DrawImGui — catches any AV from race conditions
// Also sets the CrashHandler flag to suppress VEH logging (which writes minidumps per-frame)
static void SafeDrawImGui_SEH()
{
	CrashHandler::s_InsideSEHProtection = true;
	__try
	{
		DrawImGui();
	}
	__except (EXCEPTION_EXECUTE_HANDLER)
	{
		// Silently skip this frame — mod pointers were mid-construction
	}
	CrashHandler::s_InsideSEHProtection = false;
}

// Inner dispatch helpers (contain C++ objects — cannot use __try directly)
static void DispatchDrawImGuiInner() { Global::GetGlobals()->eventSystem.dispatchEvent("DrawImGui"); }
static void DispatchDX11PresentInner(ID3D11Device* d, ID3D11DeviceContext* c, ID3D11RenderTargetView* r) { Global::GetGlobals()->eventSystem.dispatchEvent("DX11Present", d, c, r); }
static void DispatchDX12PresentInner(IDXGISwapChain* s, ID3D12GraphicsCommandList* cl) { Global::GetGlobals()->eventSystem.dispatchEvent("DX12Present", s, cl); }

// SEH wrappers for event dispatches (pure C — no C++ objects in scope)
static void SafeDispatchDrawImGui_SEH()
{
	CrashHandler::s_InsideSEHProtection = true;
	__try { DispatchDrawImGuiInner(); }
	__except (EXCEPTION_EXECUTE_HANDLER) { }
	CrashHandler::s_InsideSEHProtection = false;
}

static void SafeDispatchDX11Present_SEH(ID3D11Device* dev, ID3D11DeviceContext* ctx, ID3D11RenderTargetView* rtv)
{
	CrashHandler::s_InsideSEHProtection = true;
	__try { DispatchDX11PresentInner(dev, ctx, rtv); }
	__except (EXCEPTION_EXECUTE_HANDLER) { }
	CrashHandler::s_InsideSEHProtection = false;
}

static void SafeDispatchDX12Present_SEH(IDXGISwapChain* sc, ID3D12GraphicsCommandList* cl)
{
	CrashHandler::s_InsideSEHProtection = true;
	__try { DispatchDX12PresentInner(sc, cl); }
	__except (EXCEPTION_EXECUTE_HANDLER) { }
	CrashHandler::s_InsideSEHProtection = false;
}

void ShowCoreMods()
{
	if (!ImGui::CollapsingHeader("DLL Mods"))
		return;

	// Don't touch CoreMods until initialization is complete
	if (!Global::GetGlobals()->bCoreModsReady)
	{
		ImGui::Text("Waiting for mods to initialize...");
		return;
	}

	for (size_t i = 0; i < Global::GetGlobals()->CoreMods.size(); i++)
	{
		auto* mod = Global::GetGlobals()->CoreMods[i];
		if (!mod || !mod->IsFinishedCreating) continue;

		std::string str(mod->ModName.begin(), mod->ModName.end());
		std::string ModLabel = str + "##cm" + std::to_string(i);
		if (ImGui::TreeNode(ModLabel.c_str()))
		{

			std::string Author = "Created By: " + mod->ModAuthors;
			ImGui::Text(Author.c_str());
			ImGui::Separator();
			std::string Description = "Description: " + mod->ModDescription;
			ImGui::Text(Description.c_str());
			ImGui::Separator();
			std::string Version = "Version: " + mod->ModVersion;
			ImGui::Text(Version.c_str());
			ImGui::Separator();

			if (!mod->Dependencies.empty())
			{
				std::string deps = "Requires: ";
				for (size_t d = 0; d < mod->Dependencies.size(); d++)
				{
					if (d > 0) deps += ", ";
					deps += mod->Dependencies[d];
				}
				ImGui::TextColored(ImVec4(0.6f, 0.8f, 1.0f, 1.0f), deps.c_str());
				ImGui::Separator();
			}

			if (mod->UseMenuButton && mod->IsFinishedCreating)
			{
				std::string ButtonLabel = str + " Button" + "##cm" + std::to_string(i);
				if (ImGui::Button(ButtonLabel.c_str()))
				{
					mod->OnModMenuButtonPressed();
				}
			}

			ImGui::TreePop();
		}
	}
}

void ShowTools()
{
	if (!ImGui::CollapsingHeader("Tools"))
		return;

	ImGui::Text("FPS: %.1f", ImGui::GetIO().Framerate);

	// Show detected renderer
	const char* rendererName = "Unknown";
	switch (LoaderUI::GetUI()->ActiveRenderer)
	{
	case EDetectedRenderer::DX11: rendererName = "DirectX 11"; break;
	case EDetectedRenderer::DX12: rendererName = "DirectX 12"; break;
	default: break;
	}
	ImGui::Text("Renderer: %s", rendererName);

	ImGui::Spacing();
	if (ImGui::Button("Dump Objects"))
	{
		UITools::ObjectDump();
	}
	if (ImGui::Button("Dump Engine Info"))
	{
		UITools::EngineDump();
	}
	if (ImGui::Button("Dump World Actors"))
	{
		UITools::WorldDump();
	}

	static char Command[128];
	ImGui::Spacing();
	ImGui::Separator();
	ImGui::Text("Execute Console Command");
	ImGui::InputText("", Command, IM_ARRAYSIZE(Command));
	if (ImGui::Button("Execute"))
	{
		std::string strCommand(Command);
		std::wstring wstrCommand = std::wstring(strCommand.begin(), strCommand.end());
		UITools::ExecuteCommand(wstrCommand);
	}
}

void ShowConsoleLog()
{
	if (!ImGui::CollapsingHeader("Console Log"))
		return;

	const auto& logs = Log::GetLogArray();
	int logCount = (int)logs.size();

	ImGui::Text("%d log entries", logCount);
	ImGui::SameLine();
	static bool autoScroll = true;
	ImGui::Checkbox("Auto-scroll", &autoScroll);

	// Filter input
	static char filterBuf[128] = "";
	ImGui::InputText("Filter", filterBuf, IM_ARRAYSIZE(filterBuf));
	std::string filter(filterBuf);

	ImGui::BeginChild("LogRegion", ImVec2(0, 200 * g_UIScale), true, ImGuiWindowFlags_HorizontalScrollbar);

	for (int i = 0; i < logCount; i++)
	{
		const std::string& line = logs[i];

		// Apply filter
		if (!filter.empty() && line.find(filter) == std::string::npos)
			continue;

		// Color code by content
		ImVec4 color = ImVec4(0.8f, 0.8f, 0.8f, 1.0f); // default grey
		if (line.find("ERROR") != std::string::npos || line.find("error") != std::string::npos || line.find("Failed") != std::string::npos)
			color = ImVec4(1.0f, 0.3f, 0.3f, 1.0f); // red
		else if (line.find("WARNING") != std::string::npos || line.find("Warn") != std::string::npos)
			color = ImVec4(1.0f, 0.8f, 0.2f, 1.0f); // yellow
		else if (line.find("[cache]") != std::string::npos || line.find("[PDB]") != std::string::npos || line.find("[DbgHelp]") != std::string::npos)
			color = ImVec4(0.3f, 1.0f, 0.5f, 1.0f); // green for resolved symbols

		ImGui::PushStyleColor(ImGuiCol_Text, color);
		ImGui::TextUnformatted(line.c_str());
		ImGui::PopStyleColor();
	}

	if (autoScroll && ImGui::GetScrollY() >= ImGui::GetScrollMaxY())
		ImGui::SetScrollHereY(1.0f);

	ImGui::EndChild();
}

void DrawImGui()
{
	// Set initial window size on first use (user can still resize freely)
	ImGui::SetNextWindowSize(ImVec2(550 * g_UIScale, 600 * g_UIScale), ImGuiCond_FirstUseEver);
	ImGui::Begin("Daedalus Mod Loader", NULL, ImGuiWindowFlags_None);
	ImGui::Spacing();
	ImGui::Text("Daedalus Mod Loader V: %s", MODLOADER_VERSION);
	ShowLogicMods();
	ShowCoreMods();
	ShowTools();
	ShowConsoleLog();

	ImGui::End();
}

LRESULT CALLBACK LoaderUI::hookWndProc(HWND hWnd, UINT uMsg, WPARAM wParam, LPARAM lParam)
{
	// Always let ImGui see the message for internal tracking
	ImGui_ImplWin32_WndProcHandler(hWnd, uMsg, wParam, lParam);

	// Only consume input when the menu is actually open
	if (Global::GetGlobals()->bIsMenuOpen)
	{
		ImGuiIO& io = ImGui::GetIO();
		if (io.WantCaptureMouse || io.WantCaptureKeyboard) {
			return true;
		}
	}
	return CallWindowProc(LoaderUI::GetUI()->hGameWindowProc, hWnd, uMsg, wParam, lParam);
}


HRESULT hookResizeBuffers(IDXGISwapChain* pSwapChain, UINT BufferCount, UINT Width, UINT Height, DXGI_FORMAT NewFormat, UINT SwapChainFlags)
{
	return LoaderUI::GetUI()->LoaderResizeBuffers(pSwapChain, BufferCount, Width, Height, NewFormat, SwapChainFlags);
}

void LoaderUI::LoaderD3D11Present(IDXGISwapChain* pSwapChain, UINT SyncInterval, UINT Flags)
{
	if (LoaderUI::GetUI()->initRendering)
	{
		if (SUCCEEDED(pSwapChain->GetDevice(__uuidof(ID3D11Device), (void**)&LoaderUI::GetUI()->pDevice)) &&
			SUCCEEDED(pSwapChain->GetDevice(__uuidof(LoaderUI::GetUI()->pDevice), (void**)&LoaderUI::GetUI()->pDevice)))
		{
			LoaderUI::GetUI()->pDevice->GetImmediateContext(&LoaderUI::GetUI()->pContext);
			Log::Info("D3D11Device Initialized");
		}
		else
		{
			Log::Error("Failed to initialize D3D11Device");
		}

		ID3D11Texture2D* pRenderTargetTexture = NULL;
		if (SUCCEEDED(pSwapChain->GetBuffer(0, __uuidof(ID3D11Texture2D), (LPVOID*)&pRenderTargetTexture)) &&
			SUCCEEDED(LoaderUI::GetUI()->pDevice->CreateRenderTargetView(pRenderTargetTexture, NULL, &LoaderUI::GetUI()->pRenderTargetView)))
		{
			pRenderTargetTexture->Release();
			Log::Info("D3D11RenderTargetView Initialized");
		}
		else
		{
			Log::Error("Failed to initialize D3D11RenderTargetView");
		}

		ImGui::CreateContext();

		ImGuiIO& io = ImGui::GetIO();
		io.ConfigFlags = ImGuiConfigFlags_NoMouseCursorChange;
		ApplyImGuiScaling(pSwapChain);

		HWND hGameWindow = MEM::FindWindow(GetCurrentProcessId(), L"UnrealWindow");
		LoaderUI::GetUI()->hGameWindowProc = (WNDPROC)SetWindowLongPtr(hGameWindow, GWLP_WNDPROC, (LONG_PTR)LoaderUI::hookWndProc);
		ImGui_ImplWin32_Init(hGameWindow);

		//ImGui_ImplDX11_CreateDeviceObjects();
		ImGui_ImplDX11_Init(LoaderUI::GetUI()->pDevice, LoaderUI::GetUI()->pContext);

		LoaderUI::GetUI()->initRendering = false;
	}

	// must call before drawing
	LoaderUI::GetUI()->pContext->OMSetRenderTargets(1, &LoaderUI::GetUI()->pRenderTargetView, NULL);

	// ImGui Rendering ---------------------------------------------

	ImGui_ImplDX11_NewFrame();
	ImGui_ImplWin32_NewFrame();
	ImGui::NewFrame();
	ImGui::GetIO().MouseDrawCursor = Global::GetGlobals()->bIsMenuOpen;
	if (Global::GetGlobals()->bIsMenuOpen)
	{
		ApplyImGuiStyle();
		SafeDrawImGui_SEH();
		SafeDispatchDrawImGui_SEH();
	}

	ImGui::Render();
	ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
}

// ============================================================================
// DX12 Methods
// ============================================================================

bool LoaderUI::DetectRendererFromSwapChain(IDXGISwapChain* pSwapChain)
{
	ID3D12Device* pTestDevice = nullptr;
	if (SUCCEEDED(pSwapChain->GetDevice(__uuidof(ID3D12Device), (void**)&pTestDevice)))
	{
		pTestDevice->Release();
		return true; // DX12
	}
	return false; // DX11
}

bool LoaderUI::InitDX12Resources(IDXGISwapChain* pSwapChain)
{
	LoaderUI* ui = GetUI();

	// Get D3D12 device from swap chain
	if (FAILED(pSwapChain->GetDevice(__uuidof(ID3D12Device), (void**)&ui->pD3D12Device)))
	{
		Log::Error("DX12: Failed to get ID3D12Device from swap chain");
		return false;
	}

	// Create SRV descriptor heap for ImGui fonts
	{
		D3D12_DESCRIPTOR_HEAP_DESC desc = {};
		desc.Type = D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV;
		desc.NumDescriptors = 1;
		desc.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE;
		if (FAILED(ui->pD3D12Device->CreateDescriptorHeap(&desc, IID_PPV_ARGS(&ui->pD3D12SrvDescHeap))))
		{
			Log::Error("DX12: Failed to create SRV descriptor heap");
			return false;
		}
	}

	// Create RTV descriptor heap for back buffers
	{
		D3D12_DESCRIPTOR_HEAP_DESC desc = {};
		desc.Type = D3D12_DESCRIPTOR_HEAP_TYPE_RTV;
		desc.NumDescriptors = DX12_NUM_BACK_BUFFERS;
		desc.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_NONE;
		if (FAILED(ui->pD3D12Device->CreateDescriptorHeap(&desc, IID_PPV_ARGS(&ui->pD3D12RtvDescHeap))))
		{
			Log::Error("DX12: Failed to create RTV descriptor heap");
			return false;
		}

		SIZE_T rtvDescSize = ui->pD3D12Device->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_RTV);
		D3D12_CPU_DESCRIPTOR_HANDLE rtvHandle = ui->pD3D12RtvDescHeap->GetCPUDescriptorHandleForHeapStart();
		for (int i = 0; i < DX12_NUM_BACK_BUFFERS; i++)
		{
			ui->d3d12RtvHandles[i] = rtvHandle;
			rtvHandle.ptr += rtvDescSize;
		}
	}

	// Create command allocators
	for (int i = 0; i < DX12_NUM_FRAMES_IN_FLIGHT; i++)
	{
		if (FAILED(ui->pD3D12Device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&ui->pD3D12CommandAllocators[i]))))
		{
			Log::Error("DX12: Failed to create command allocator %d", i);
			return false;
		}
	}

	// Create command list
	if (FAILED(ui->pD3D12Device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, ui->pD3D12CommandAllocators[0], NULL, IID_PPV_ARGS(&ui->pD3D12CommandList))))
	{
		Log::Error("DX12: Failed to create command list");
		return false;
	}
	ui->pD3D12CommandList->Close();

	// Create fence for synchronization
	if (FAILED(ui->pD3D12Device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&ui->pD3D12Fence))))
	{
		Log::Error("DX12: Failed to create fence");
		return false;
	}
	ui->d3d12FenceEvent = CreateEvent(NULL, FALSE, FALSE, NULL);

	// Get back buffers and create RTVs
	IDXGISwapChain3* pSwapChain3 = nullptr;
	if (SUCCEEDED(pSwapChain->QueryInterface(IID_PPV_ARGS(&pSwapChain3))))
	{
		for (int i = 0; i < DX12_NUM_BACK_BUFFERS; i++)
		{
			pSwapChain3->GetBuffer(i, IID_PPV_ARGS(&ui->pD3D12BackBuffers[i]));
			ui->pD3D12Device->CreateRenderTargetView(ui->pD3D12BackBuffers[i], NULL, ui->d3d12RtvHandles[i]);
		}
		pSwapChain3->Release();
	}

	Log::Info("DX12: Resources initialized successfully");
	return true;
}

void LoaderUI::CleanupDX12Resources()
{
	LoaderUI* ui = GetUI();
	WaitForLastSubmittedFrame();

	for (int i = 0; i < DX12_NUM_BACK_BUFFERS; i++)
	{
		if (ui->pD3D12BackBuffers[i]) { ui->pD3D12BackBuffers[i]->Release(); ui->pD3D12BackBuffers[i] = nullptr; }
	}
	if (ui->pD3D12CommandList) { ui->pD3D12CommandList->Release(); ui->pD3D12CommandList = nullptr; }
	for (int i = 0; i < DX12_NUM_FRAMES_IN_FLIGHT; i++)
	{
		if (ui->pD3D12CommandAllocators[i]) { ui->pD3D12CommandAllocators[i]->Release(); ui->pD3D12CommandAllocators[i] = nullptr; }
	}
	if (ui->pD3D12Fence) { ui->pD3D12Fence->Release(); ui->pD3D12Fence = nullptr; }
	if (ui->d3d12FenceEvent) { CloseHandle(ui->d3d12FenceEvent); ui->d3d12FenceEvent = nullptr; }
	if (ui->pD3D12SrvDescHeap) { ui->pD3D12SrvDescHeap->Release(); ui->pD3D12SrvDescHeap = nullptr; }
	if (ui->pD3D12RtvDescHeap) { ui->pD3D12RtvDescHeap->Release(); ui->pD3D12RtvDescHeap = nullptr; }
	if (ui->pD3D12Device) { ui->pD3D12Device->Release(); ui->pD3D12Device = nullptr; }
}

void LoaderUI::WaitForLastSubmittedFrame()
{
	LoaderUI* ui = GetUI();
	if (!ui->pD3D12Fence || !ui->pD3D12CommandQueue)
		return;

	UINT frameIdx = ui->d3d12FrameIndex % DX12_NUM_FRAMES_IN_FLIGHT;
	UINT64 fenceValue = ui->d3d12FenceValues[frameIdx];

	ui->pD3D12CommandQueue->Signal(ui->pD3D12Fence, fenceValue);
	if (ui->pD3D12Fence->GetCompletedValue() < fenceValue)
	{
		ui->pD3D12Fence->SetEventOnCompletion(fenceValue, ui->d3d12FenceEvent);
		WaitForSingleObject(ui->d3d12FenceEvent, 5000);
	}
}

void LoaderUI::DX12ResizeCleanup()
{
	LoaderUI* ui = GetUI();
	for (int i = 0; i < DX12_NUM_BACK_BUFFERS; i++)
	{
		if (ui->pD3D12BackBuffers[i]) { ui->pD3D12BackBuffers[i]->Release(); ui->pD3D12BackBuffers[i] = nullptr; }
	}
}

void LoaderUI::LoaderD3D12Present(IDXGISwapChain* pSwapChain, UINT SyncInterval, UINT Flags)
{
	LoaderUI* ui = GetUI();

	// Need the command queue before we can render
	if (!ui->d3d12QueueCaptured || !ui->pD3D12CommandQueue)
		return;

	if (ui->initRendering)
	{
		if (!InitDX12Resources(pSwapChain))
		{
			Log::Error("DX12: Init failed, falling back to no overlay");
			return;
		}

		ImGui::CreateContext();
		ImGuiIO& io = ImGui::GetIO();
		io.ConfigFlags = ImGuiConfigFlags_NoMouseCursorChange;
		ApplyImGuiScaling(pSwapChain);

		HWND hGameWindow = MEM::FindWindow(GetCurrentProcessId(), L"UnrealWindow");
		ui->hGameWindowProc = (WNDPROC)SetWindowLongPtr(hGameWindow, GWLP_WNDPROC, (LONG_PTR)LoaderUI::hookWndProc);
		ImGui_ImplWin32_Init(hGameWindow);
		ImGui_ImplDX12_Init(ui->pD3D12Device, DX12_NUM_FRAMES_IN_FLIGHT,
			DXGI_FORMAT_R8G8B8A8_UNORM, ui->pD3D12SrvDescHeap,
			ui->pD3D12SrvDescHeap->GetCPUDescriptorHandleForHeapStart(),
			ui->pD3D12SrvDescHeap->GetGPUDescriptorHandleForHeapStart());

		ui->initRendering = false;
		Log::Info("DX12: ImGui initialized");
	}

	// Get current back buffer index
	IDXGISwapChain3* pSwapChain3 = nullptr;
	UINT backBufferIdx = 0;
	if (SUCCEEDED(pSwapChain->QueryInterface(IID_PPV_ARGS(&pSwapChain3))))
	{
		backBufferIdx = pSwapChain3->GetCurrentBackBufferIndex();
		pSwapChain3->Release();
	}

	UINT frameIdx = ui->d3d12FrameIndex % DX12_NUM_FRAMES_IN_FLIGHT;

	// Wait for this frame's previous work to complete
	if (ui->pD3D12Fence->GetCompletedValue() < ui->d3d12FenceValues[frameIdx])
	{
		ui->pD3D12Fence->SetEventOnCompletion(ui->d3d12FenceValues[frameIdx], ui->d3d12FenceEvent);
		WaitForSingleObject(ui->d3d12FenceEvent, 5000);
	}

	// Reset allocator and command list
	ui->pD3D12CommandAllocators[frameIdx]->Reset();
	ui->pD3D12CommandList->Reset(ui->pD3D12CommandAllocators[frameIdx], NULL);

	// Transition back buffer to render target
	D3D12_RESOURCE_BARRIER barrier = {};
	barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
	barrier.Transition.pResource = ui->pD3D12BackBuffers[backBufferIdx];
	barrier.Transition.StateBefore = D3D12_RESOURCE_STATE_PRESENT;
	barrier.Transition.StateAfter = D3D12_RESOURCE_STATE_RENDER_TARGET;
	barrier.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
	ui->pD3D12CommandList->ResourceBarrier(1, &barrier);

	ui->pD3D12CommandList->OMSetRenderTargets(1, &ui->d3d12RtvHandles[backBufferIdx], FALSE, NULL);
	ui->pD3D12CommandList->SetDescriptorHeaps(1, &ui->pD3D12SrvDescHeap);

	// ImGui rendering
	ImGui_ImplDX12_NewFrame();
	ImGui_ImplWin32_NewFrame();
	ImGui::NewFrame();
	ImGui::GetIO().MouseDrawCursor = Global::GetGlobals()->bIsMenuOpen;
	if (Global::GetGlobals()->bIsMenuOpen)
	{
		ApplyImGuiStyle();
		SafeDrawImGui_SEH();
		SafeDispatchDrawImGui_SEH();
	}
	ImGui::Render();
	ImGui_ImplDX12_RenderDrawData(ImGui::GetDrawData(), ui->pD3D12CommandList);

	// Transition back to present
	barrier.Transition.StateBefore = D3D12_RESOURCE_STATE_RENDER_TARGET;
	barrier.Transition.StateAfter = D3D12_RESOURCE_STATE_PRESENT;
	ui->pD3D12CommandList->ResourceBarrier(1, &barrier);
	ui->pD3D12CommandList->Close();

	// Execute
	ID3D12CommandList* ppCmdLists[] = { ui->pD3D12CommandList };
	ui->pD3D12CommandQueue->ExecuteCommandLists(1, ppCmdLists);

	ui->d3d12FenceValues[frameIdx]++;
	ui->pD3D12CommandQueue->Signal(ui->pD3D12Fence, ui->d3d12FenceValues[frameIdx]);
	ui->d3d12FrameIndex++;
}

// ============================================================================
// ExecuteCommandLists hook — captures the game's DX12 command queue
// ============================================================================

static void __stdcall hookExecuteCommandLists(ID3D12CommandQueue* pQueue, UINT NumCommandLists, ID3D12CommandList* const* ppCommandLists)
{
	LoaderUI* ui = LoaderUI::GetUI();
	if (!ui->d3d12QueueCaptured && pQueue)
	{
		D3D12_COMMAND_QUEUE_DESC desc = pQueue->GetDesc();
		if (desc.Type == D3D12_COMMAND_LIST_TYPE_DIRECT)
		{
			ui->pD3D12CommandQueue = pQueue;
			ui->d3d12QueueCaptured = true;
			Log::Info("DX12: Captured game command queue: 0x%p", pQueue);
		}
	}
	// Call original
	ui->pOriginalExecuteCommandLists(pQueue, NumCommandLists, ppCommandLists);
}

// ============================================================================
// Shared style setup (extracted to avoid duplication)
// ============================================================================

static bool s_StyleInitialized = false;
static void ApplyImGuiStyle()
{
	if (s_StyleInitialized)
		return;
	s_StyleInitialized = true;

	ImGuiStyle* style = &ImGui::GetStyle();
	ImVec4* colors = style->Colors;
	colors[ImGuiCol_Text] = ImVec4(1.000f, 1.000f, 1.000f, 1.000f);
	colors[ImGuiCol_TextDisabled] = ImVec4(0.500f, 0.500f, 0.500f, 1.000f);
	colors[ImGuiCol_WindowBg] = ImVec4(0.180f, 0.180f, 0.180f, 1.000f);
	colors[ImGuiCol_ChildBg] = ImVec4(0.280f, 0.280f, 0.280f, 0.000f);
	colors[ImGuiCol_PopupBg] = ImVec4(0.313f, 0.313f, 0.313f, 1.000f);
	colors[ImGuiCol_Border] = ImVec4(0.266f, 0.266f, 0.266f, 1.000f);
	colors[ImGuiCol_BorderShadow] = ImVec4(0.000f, 0.000f, 0.000f, 0.000f);
	colors[ImGuiCol_FrameBg] = ImVec4(0.160f, 0.160f, 0.160f, 1.000f);
	colors[ImGuiCol_FrameBgHovered] = ImVec4(0.200f, 0.200f, 0.200f, 1.000f);
	colors[ImGuiCol_FrameBgActive] = ImVec4(0.280f, 0.280f, 0.280f, 1.000f);
	colors[ImGuiCol_TitleBg] = ImVec4(0.148f, 0.148f, 0.148f, 1.000f);
	colors[ImGuiCol_TitleBgActive] = ImVec4(0.148f, 0.148f, 0.148f, 1.000f);
	colors[ImGuiCol_TitleBgCollapsed] = ImVec4(0.148f, 0.148f, 0.148f, 1.000f);
	colors[ImGuiCol_MenuBarBg] = ImVec4(0.195f, 0.195f, 0.195f, 1.000f);
	colors[ImGuiCol_ScrollbarBg] = ImVec4(0.160f, 0.160f, 0.160f, 1.000f);
	colors[ImGuiCol_ScrollbarGrab] = ImVec4(0.277f, 0.277f, 0.277f, 1.000f);
	colors[ImGuiCol_ScrollbarGrabHovered] = ImVec4(0.300f, 0.300f, 0.300f, 1.000f);
	colors[ImGuiCol_ScrollbarGrabActive] = ImVec4(1.000f, 0.391f, 0.000f, 1.000f);
	colors[ImGuiCol_CheckMark] = ImVec4(1.000f, 1.000f, 1.000f, 1.000f);
	colors[ImGuiCol_SliderGrab] = ImVec4(0.391f, 0.391f, 0.391f, 1.000f);
	colors[ImGuiCol_SliderGrabActive] = ImVec4(1.000f, 0.391f, 0.000f, 1.000f);
	colors[ImGuiCol_Button] = ImVec4(1.000f, 1.000f, 1.000f, 0.000f);
	colors[ImGuiCol_ButtonHovered] = ImVec4(1.000f, 1.000f, 1.000f, 0.156f);
	colors[ImGuiCol_ButtonActive] = ImVec4(1.000f, 1.000f, 1.000f, 0.391f);
	colors[ImGuiCol_Header] = ImVec4(0.313f, 0.313f, 0.313f, 1.000f);
	colors[ImGuiCol_HeaderHovered] = ImVec4(0.469f, 0.469f, 0.469f, 1.000f);
	colors[ImGuiCol_HeaderActive] = ImVec4(0.469f, 0.469f, 0.469f, 1.000f);
	colors[ImGuiCol_Separator] = colors[ImGuiCol_Border];
	colors[ImGuiCol_SeparatorHovered] = ImVec4(0.391f, 0.391f, 0.391f, 1.000f);
	colors[ImGuiCol_SeparatorActive] = ImVec4(1.000f, 0.391f, 0.000f, 1.000f);
	colors[ImGuiCol_ResizeGrip] = ImVec4(1.000f, 1.000f, 1.000f, 0.250f);
	colors[ImGuiCol_ResizeGripHovered] = ImVec4(1.000f, 1.000f, 1.000f, 0.670f);
	colors[ImGuiCol_ResizeGripActive] = ImVec4(1.000f, 0.391f, 0.000f, 1.000f);
	colors[ImGuiCol_Tab] = ImVec4(0.098f, 0.098f, 0.098f, 1.000f);
	colors[ImGuiCol_TabHovered] = ImVec4(0.352f, 0.352f, 0.352f, 1.000f);
	colors[ImGuiCol_TabActive] = ImVec4(0.195f, 0.195f, 0.195f, 1.000f);
	colors[ImGuiCol_TabUnfocused] = ImVec4(0.098f, 0.098f, 0.098f, 1.000f);
	colors[ImGuiCol_TabUnfocusedActive] = ImVec4(0.195f, 0.195f, 0.195f, 1.000f);
	colors[ImGuiCol_PlotLines] = ImVec4(0.469f, 0.469f, 0.469f, 1.000f);
	colors[ImGuiCol_PlotLinesHovered] = ImVec4(1.000f, 0.391f, 0.000f, 1.000f);
	colors[ImGuiCol_PlotHistogram] = ImVec4(0.586f, 0.586f, 0.586f, 1.000f);
	colors[ImGuiCol_PlotHistogramHovered] = ImVec4(1.000f, 0.391f, 0.000f, 1.000f);
	colors[ImGuiCol_TextSelectedBg] = ImVec4(1.000f, 1.000f, 1.000f, 0.156f);
	colors[ImGuiCol_DragDropTarget] = ImVec4(1.000f, 0.391f, 0.000f, 1.000f);
	colors[ImGuiCol_NavHighlight] = ImVec4(1.000f, 0.391f, 0.000f, 1.000f);
	colors[ImGuiCol_NavWindowingHighlight] = ImVec4(1.000f, 0.391f, 0.000f, 1.000f);
	colors[ImGuiCol_NavWindowingDimBg] = ImVec4(0.000f, 0.000f, 0.000f, 0.586f);
	colors[ImGuiCol_ModalWindowDimBg] = ImVec4(0.000f, 0.000f, 0.000f, 0.586f);

	style->ChildRounding = 4.0f;
	style->FrameBorderSize = 1.0f;
	style->FrameRounding = 2.0f;
	style->GrabMinSize = 7.0f;
	style->PopupRounding = 2.0f;
	style->ScrollbarRounding = 12.0f;
	style->ScrollbarSize = 13.0f;
	style->TabBorderSize = 1.0f;
	style->TabRounding = 0.0f;
	style->WindowRounding = 4.0f;

	// Scale all widget sizes for high-res displays
	if (g_UIScale > 1.0f)
		style->ScaleAllSizes(g_UIScale);
}

// ============================================================================
// DX11 Present Hook
// ============================================================================

HRESULT(*D3D11Present)(IDXGISwapChain* pSwapChain, UINT SyncInterval, UINT Flags);
HRESULT __stdcall hookD3D11Present(IDXGISwapChain* pSwapChain, UINT SyncInterval, UINT Flags)
{
	LoaderUI* UI = LoaderUI::GetUI();

	// Auto-detect renderer on first call (or use forced config)
	if (UI->ActiveRenderer == EDetectedRenderer::Unknown)
	{
		ERendererBackend backend = DaedalusConfig::GetConfig()->GetRendererBackend();
		if (backend == ERendererBackend::ForceDX11)
		{
			UI->ActiveRenderer = EDetectedRenderer::DX11;
			Log::Info("Renderer forced: DirectX 11 (config)");
		}
		else if (backend == ERendererBackend::ForceDX12)
		{
			UI->ActiveRenderer = EDetectedRenderer::DX12;
			Log::Info("Renderer forced: DirectX 12 (config)");
		}
		else if (LoaderUI::DetectRendererFromSwapChain(pSwapChain))
		{
			UI->ActiveRenderer = EDetectedRenderer::DX12;
			Log::Info("Renderer detected: DirectX 12");
		}
		else
		{
			UI->ActiveRenderer = EDetectedRenderer::DX11;
			Log::Info("Renderer detected: DirectX 11");
		}
		DaedalusConfig::GetConfig()->SetDetectedRenderer(UI->ActiveRenderer);
	}

	if (UI->ActiveRenderer == EDetectedRenderer::DX12)
	{
		UI->LoaderD3D12Present(pSwapChain, SyncInterval, Flags);
		SafeDispatchDX12Present_SEH(pSwapChain, UI->pD3D12CommandList);
	}
	else
	{
		UI->LoaderD3D11Present(pSwapChain, SyncInterval, Flags);
		SafeDispatchDX11Present_SEH(UI->pDevice, UI->pContext, UI->pRenderTargetView);
	}

	return D3D11Present(pSwapChain, SyncInterval, Flags);
}

DWORD __stdcall InitDX11Hook(LPVOID)
{
	Log::Info("Setting up D3D11Present hook");

	HMODULE hDXGIDLL = 0;
	do
	{
		hDXGIDLL = GetModuleHandle(L"dxgi.dll");
		Sleep(100);
	} while (!hDXGIDLL);
	Sleep(100);

	IDXGISwapChain* pSwapChain;

	WNDCLASSEXA wc = { sizeof(WNDCLASSEX), CS_CLASSDC, DefWindowProc, 0L, 0L, GetModuleHandleA(NULL), NULL, NULL, NULL, NULL, "DX", NULL };
	RegisterClassExA(&wc);

	HWND hWnd = CreateWindowA("DX", NULL, WS_OVERLAPPEDWINDOW, 100, 100, 300, 300, NULL, NULL, wc.hInstance, NULL);

	D3D_FEATURE_LEVEL requestedLevels[] = { D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_1 };
	D3D_FEATURE_LEVEL obtainedLevel;
	ID3D11Device* d3dDevice = nullptr;
	ID3D11DeviceContext* d3dContext = nullptr;

	DXGI_SWAP_CHAIN_DESC scd;
	ZeroMemory(&scd, sizeof(scd));
	scd.BufferCount = 1;
	scd.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
	scd.BufferDesc.Scaling = DXGI_MODE_SCALING_UNSPECIFIED;
	scd.BufferDesc.ScanlineOrdering = DXGI_MODE_SCANLINE_ORDER_UNSPECIFIED;
	scd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;

	scd.Flags = DXGI_SWAP_CHAIN_FLAG_ALLOW_MODE_SWITCH;
	scd.OutputWindow = hWnd;
	scd.SampleDesc.Count = 1;
	scd.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;
	scd.Windowed = ((GetWindowLongPtr(hWnd, GWL_STYLE) & WS_POPUP) != 0) ? false : true;

	scd.BufferDesc.Width = 1;
	scd.BufferDesc.Height = 1;
	scd.BufferDesc.RefreshRate.Numerator = 0;
	scd.BufferDesc.RefreshRate.Denominator = 1;

	UINT createFlags = 0;
#ifdef _DEBUG
	createFlags |= D3D11_CREATE_DEVICE_DEBUG;
#endif

	IDXGISwapChain* d3dSwapChain = 0;

	if (FAILED(D3D11CreateDeviceAndSwapChain(
		nullptr,
		D3D_DRIVER_TYPE_HARDWARE,
		nullptr,
		createFlags,
		requestedLevels,
		sizeof(requestedLevels) / sizeof(D3D_FEATURE_LEVEL),
		D3D11_SDK_VERSION,
		&scd,
		&pSwapChain,
		&LoaderUI::GetUI()->pDevice,
		&obtainedLevel,
		&LoaderUI::GetUI()->pContext)))
	{
		Log::Error("Failed to create D3D device and swapchain");
		return NULL;
	}

	LoaderUI::GetUI()->pSwapChainVtable = (DWORD_PTR*)pSwapChain;
	LoaderUI::GetUI()->pSwapChainVtable = (DWORD_PTR*)LoaderUI::GetUI()->pSwapChainVtable[0];
	LoaderUI::GetUI()->phookDXGIPresent = (LoaderUI::DXGIPresentHook)LoaderUI::GetUI()->pSwapChainVtable[8];
	MinHook::Add((DWORD64)LoaderUI::GetUI()->pSwapChainVtable[13], &hookResizeBuffers, &LoaderUI::GetUI()->ResizeBuffers, "DX11-ResizeBuffers");
	MinHook::Add((DWORD64)LoaderUI::GetUI()->phookDXGIPresent, &hookD3D11Present, &D3D11Present, "DXGI-Present");

	DWORD dPresentwOld;
	DWORD dResizeOld;
	VirtualProtect(LoaderUI::GetUI()->phookDXGIPresent, 2, PAGE_EXECUTE_READWRITE, &dPresentwOld);
	VirtualProtect((LPVOID)LoaderUI::GetUI()->pSwapChainVtable[13], 2, PAGE_EXECUTE_READWRITE, &dResizeOld);

	// Also hook ExecuteCommandLists for DX12 command queue capture
	{
		ID3D12Device* pTempDevice = nullptr;
		if (SUCCEEDED(D3D12CreateDevice(nullptr, D3D_FEATURE_LEVEL_11_0, IID_PPV_ARGS(&pTempDevice))))
		{
			D3D12_COMMAND_QUEUE_DESC queueDesc = {};
			queueDesc.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
			ID3D12CommandQueue* pTempQueue = nullptr;
			if (SUCCEEDED(pTempDevice->CreateCommandQueue(&queueDesc, IID_PPV_ARGS(&pTempQueue))))
			{
				DWORD_PTR* pQueueVtable = *(DWORD_PTR**)pTempQueue;
				// ExecuteCommandLists is index 10 in ID3D12CommandQueue vtable
				MinHook::Add((DWORD64)pQueueVtable[10], &hookExecuteCommandLists,
					&LoaderUI::GetUI()->pOriginalExecuteCommandLists, "DX12-ExecuteCommandLists");
				Log::Info("DX12: ExecuteCommandLists hook installed");
				pTempQueue->Release();
			}
			pTempDevice->Release();
		}
		else
		{
			Log::Info("DX12: D3D12CreateDevice not available (DX11 only system)");
		}
	}

	while (true)
	{
		Sleep(10);
	}

	LoaderUI::GetUI()->pDevice->Release();
	LoaderUI::GetUI()->pContext->Release();
	pSwapChain->Release();
	return NULL;
}

void LoaderUI::HookDX()
{
	if (!LoaderUI::GetUI()->IsDXHooked)
	{
		CreateThread(NULL, 0, InitDX11Hook, NULL, 0, NULL);
		LoaderUI::GetUI()->IsDXHooked = true;
	}
}

DWORD __stdcall LogicThread(LPVOID)
{
	UILogicTick();
	return NULL;
}


void LoaderUI::CreateUILogicThread()
{
	Log::Info("CreateUILogicThread Called");
	CreateThread(0, 0, LogicThread, 0, 0, 0);
}