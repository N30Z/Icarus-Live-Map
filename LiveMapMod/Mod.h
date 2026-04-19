#pragma once
#pragma warning(disable: 4251)  // std::string/vector als DLL-Interface - bekannte Warnung
#include <string>
#include <vector>

// Forward declarations (no DirectX/UE4 headers needed)
namespace UE4 { class AActor; }
struct ID3D11Device;
struct ID3D11DeviceContext;
struct ID3D11RenderTargetView;
struct IDXGISwapChain;
struct ID3D12GraphicsCommandList;

#define LOADER_API __declspec(dllimport)

// Matches DaedalusLoader.dll ABI exactly (same field order + virtual table order)
class LOADER_API Mod
{
public:
    std::string ModName;
    std::string ModVersion;
    std::string ModDescription;
    std::string ModAuthors;
    std::string ModLoaderVersion;
    bool UseMenuButton      = false;
    bool IsFinishedCreating = false;
    std::vector<std::string> Dependencies;

    void SetupHooks();

    virtual void InitializeMod();
    virtual void InitGameState();
    virtual void DrawImGui();
    virtual void BeginPlay(UE4::AActor* Actor);
    virtual void PostBeginPlay(std::wstring ModActorName, UE4::AActor* Actor);
    virtual void DX11Present(ID3D11Device* pDevice, ID3D11DeviceContext* pContext, ID3D11RenderTargetView* pRenderTargetView);
    virtual void DX12Present(IDXGISwapChain* pSwapChain, ID3D12GraphicsCommandList* pCommandList);
    virtual void OnModMenuButtonPressed();

    void CompleteModCreation();

    static Mod* ModRef;
};
