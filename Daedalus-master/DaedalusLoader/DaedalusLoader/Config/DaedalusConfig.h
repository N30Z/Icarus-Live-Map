#pragma once
#include <string>
#include <fstream>
#include <Windows.h>
#include "../UMLDefs.h"

// Renderer backend selection
enum class ERendererBackend : int
{
	Auto = 0,       // Auto-detect at runtime (default)
	ForceDX11 = 1,  // Force DX11 backend
	ForceDX12 = 2   // Force DX12 backend
};

// Detected renderer (set at runtime)
enum class EDetectedRenderer : int
{
	Unknown = 0,
	DX11 = 1,
	DX12 = 2
};

class LOADER_API DaedalusConfig
{
public:
	static DaedalusConfig* GetConfig();

	// Load config from file, creates default if not found
	void LoadConfig();

	// Save current config to file
	void SaveConfig();

	// Getters
	ERendererBackend GetRendererBackend() const { return RendererBackend; }
	EDetectedRenderer GetDetectedRenderer() const { return DetectedRenderer; }
	int GetMenuKey() const { return MenuKey; }

	// Setters
	void SetDetectedRenderer(EDetectedRenderer renderer) { DetectedRenderer = renderer; }

private:
	DaedalusConfig() = default;
	static DaedalusConfig* Instance;

	// Config values
	ERendererBackend RendererBackend = ERendererBackend::Auto;
	EDetectedRenderer DetectedRenderer = EDetectedRenderer::Unknown;
	int MenuKey = VK_F1;

	// Helpers
	std::string GetConfigPath();
	std::string Trim(const std::string& str);
};
