#include "DaedalusConfig.h"
#include "../Utilities/Logger.h"
#include <algorithm>
#include <sstream>
#include <map>

DaedalusConfig* DaedalusConfig::Instance = nullptr;

DaedalusConfig* DaedalusConfig::GetConfig()
{
	if (!Instance)
	{
		Instance = new DaedalusConfig();
	}
	return Instance;
}

std::string DaedalusConfig::GetConfigPath()
{
	char modulePath[MAX_PATH];
	GetModuleFileNameA(NULL, modulePath, MAX_PATH);
	std::string path(modulePath);
	size_t lastSlash = path.find_last_of("\\/");
	if (lastSlash != std::string::npos)
		path = path.substr(0, lastSlash + 1);
	return path + "daedalus.ini";
}

std::string DaedalusConfig::Trim(const std::string& str)
{
	size_t first = str.find_first_not_of(" \t\r\n");
	if (first == std::string::npos) return "";
	size_t last = str.find_last_not_of(" \t\r\n");
	return str.substr(first, last - first + 1);
}

void DaedalusConfig::LoadConfig()
{
	std::string configPath = GetConfigPath();
	std::ifstream file(configPath);

	if (!file.is_open())
	{
		Log::Info("No config file found, creating default at: %s", configPath.c_str());
		SaveConfig();
		return;
	}

	Log::Info("Loading config from: %s", configPath.c_str());

	std::map<std::string, std::string> values;
	std::string line;
	while (std::getline(file, line))
	{
		line = Trim(line);
		if (line.empty() || line[0] == '#' || line[0] == ';' || line[0] == '[')
			continue;

		size_t eq = line.find('=');
		if (eq == std::string::npos) continue;

		std::string key = Trim(line.substr(0, eq));
		std::string val = Trim(line.substr(eq + 1));

		// Strip inline comments
		size_t comment = val.find('#');
		if (comment != std::string::npos)
			val = Trim(val.substr(0, comment));
		comment = val.find(';');
		if (comment != std::string::npos)
			val = Trim(val.substr(0, comment));

		// Lowercase key for comparison
		std::string keyLower = key;
		std::transform(keyLower.begin(), keyLower.end(), keyLower.begin(), ::tolower);
		values[keyLower] = val;
	}
	file.close();

	// Parse renderer backend
	if (values.count("renderer"))
	{
		std::string val = values["renderer"];
		std::transform(val.begin(), val.end(), val.begin(), ::tolower);
		if (val == "auto" || val == "0")
			RendererBackend = ERendererBackend::Auto;
		else if (val == "dx11" || val == "1")
			RendererBackend = ERendererBackend::ForceDX11;
		else if (val == "dx12" || val == "2")
			RendererBackend = ERendererBackend::ForceDX12;
		else
			Log::Warn("Unknown renderer value '%s', using auto", val.c_str());
	}

	// Parse menu key
	if (values.count("menukey"))
	{
		try
		{
			int key = std::stoi(values["menukey"], nullptr, 0);
			if (key > 0 && key < 256)
				MenuKey = key;
		}
		catch (...) {}
	}

	const char* rendererNames[] = { "Auto", "ForceDX11", "ForceDX12" };
	Log::Info("Config loaded - Renderer: %s, MenuKey: 0x%X",
		rendererNames[(int)RendererBackend], MenuKey);
}

void DaedalusConfig::SaveConfig()
{
	std::string configPath = GetConfigPath();
	std::ofstream file(configPath);

	if (!file.is_open())
	{
		Log::Error("Failed to save config to: %s", configPath.c_str());
		return;
	}

	file << "# Daedalus Mod Loader Configuration" << std::endl;
	file << "# =================================" << std::endl;
	file << std::endl;
	file << "[Renderer]" << std::endl;
	file << "# Renderer backend: auto, dx11, dx12" << std::endl;
	file << "# auto = detect at runtime (recommended)" << std::endl;
	file << "# dx11 = force DirectX 11 backend" << std::endl;
	file << "# dx12 = force DirectX 12 backend" << std::endl;

	switch (RendererBackend)
	{
	case ERendererBackend::ForceDX11: file << "renderer = dx11" << std::endl; break;
	case ERendererBackend::ForceDX12: file << "renderer = dx12" << std::endl; break;
	default: file << "renderer = auto" << std::endl; break;
	}

	file << std::endl;
	file << "[Input]" << std::endl;
	file << "# Menu toggle key (VK code, hex or decimal)" << std::endl;
	file << "# Default: 0x70 (F1)" << std::endl;
	file << "# See: https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes" << std::endl;
	file << "menukey = 0x" << std::hex << MenuKey << std::endl;

	file.close();
	Log::Info("Config saved to: %s", configPath.c_str());
}
