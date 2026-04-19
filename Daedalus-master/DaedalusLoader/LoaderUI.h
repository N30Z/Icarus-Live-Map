#pragma once
#include <Windows.h>
#include <inttypes.h>
#include <string>
#include <dxgi.h>
#include <dxgi1_4.h>
#include <d3d11.h>
#include <D3D11Shader.h>
#include <D3Dcompiler.h>
#include <d3d12.h>
#include "ImGui/imgui.h"
#include "ImGui/imgui_internal.h"
#include "ImGui/imgui_impl_dx11.h"
#include "ImGui/imgui_impl_dx12.h"
#include "ImGui/imgui_impl_win32.h"
#include "DaedalusLoader/Config/DaedalusConfig.h"

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "d3d12.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "D3dcompiler.lib")

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam);

// Number of frames in flight for DX12
static constexpr int DX12_NUM_FRAMES_IN_FLIGHT = 3;
static constexpr int DX12_NUM_BACK_BUFFERS = 3;

class LOADER_API LoaderUI
{
public:
	// --- DXGI (shared) ---
	typedef HRESULT(__stdcall* DXGIPresentHook)(IDXGISwapChain* pSwapChain, UINT SyncInterval, UINT Flags);
	DXGIPresentHook phookDXGIPresent = NULL;
	DWORD_PTR* pSwapChainVtable = NULL;
	HRESULT(*ResizeBuffers)(IDXGISwapChain*, UINT, UINT, UINT, DXGI_FORMAT, UINT) = NULL;

	// --- DX11 ---
	ID3D11Device* pDevice = NULL;
	ID3D11DeviceContext* pContext = NULL;
	ID3D11RenderTargetView* pRenderTargetView = NULL;
	D3D11_VIEWPORT viewport;

	// --- DX12 ---
	ID3D12Device* pD3D12Device = NULL;
	ID3D12CommandQueue* pD3D12CommandQueue = NULL;       // Captured from game via ExecuteCommandLists hook
	ID3D12DescriptorHeap* pD3D12SrvDescHeap = NULL;
	ID3D12DescriptorHeap* pD3D12RtvDescHeap = NULL;
	ID3D12GraphicsCommandList* pD3D12CommandList = NULL;
	ID3D12CommandAllocator* pD3D12CommandAllocators[DX12_NUM_FRAMES_IN_FLIGHT] = {};
	ID3D12Resource* pD3D12BackBuffers[DX12_NUM_BACK_BUFFERS] = {};
	D3D12_CPU_DESCRIPTOR_HANDLE d3d12RtvHandles[DX12_NUM_BACK_BUFFERS] = {};
	ID3D12Fence* pD3D12Fence = NULL;
	HANDLE d3d12FenceEvent = NULL;
	UINT64 d3d12FenceValues[DX12_NUM_FRAMES_IN_FLIGHT] = {};
	UINT d3d12FrameIndex = 0;
	bool d3d12QueueCaptured = false;  // True once we've captured the game's command queue

	// ExecuteCommandLists hook (for capturing game's command queue)
	typedef void(__stdcall* ExecuteCommandListsFn)(ID3D12CommandQueue*, UINT, ID3D12CommandList* const*);
	ExecuteCommandListsFn pOriginalExecuteCommandLists = NULL;

	// --- Shared state ---
	WNDPROC hGameWindowProc = NULL;
	float screenCenterX = 0;
	float screenCenterY = 0;
	bool initRendering = true;
	bool IsDXHooked = false;
	EDetectedRenderer ActiveRenderer = EDetectedRenderer::Unknown;

	// --- DX11 methods ---
	HRESULT LoaderResizeBuffers(IDXGISwapChain* pSwapChain, UINT BufferCount, UINT Width, UINT Height, DXGI_FORMAT NewFormat, UINT SwapChainFlags);
	void LoaderD3D11Present(IDXGISwapChain* pSwapChain, UINT SyncInterval, UINT Flags);

	// --- DX12 methods ---
	bool InitDX12Resources(IDXGISwapChain* pSwapChain);
	void CleanupDX12Resources();
	void LoaderD3D12Present(IDXGISwapChain* pSwapChain, UINT SyncInterval, UINT Flags);
	void WaitForLastSubmittedFrame();
	void DX12ResizeCleanup();

	// --- Shared methods ---
	static LRESULT CALLBACK hookWndProc(HWND hWnd, UINT uMsg, WPARAM wParam, LPARAM lParam);
	void CreateUILogicThread();
	static LoaderUI* GetUI();
	static void HookDX();

	// Runtime detection: returns true if DX12, false if DX11
	static bool DetectRendererFromSwapChain(IDXGISwapChain* pSwapChain);

private:
	static LoaderUI* UI;
};
