#include "OffsetCache.h"
#include "../Utilities/Logger.h"
#include "../GameInfo/GameInfo.h"
#include <fstream>
#include <sstream>
#include <filesystem>

// Static member definitions
std::unordered_map<std::string, OffsetCache::CacheEntry> OffsetCache::s_Entries;
std::string OffsetCache::s_Fingerprint;
DWORD64 OffsetCache::s_BaseAddress = 0;
bool OffsetCache::s_Valid = false;

std::string OffsetCache::GetCachePath()
{
	char exePath[MAX_PATH];
	GetModuleFileNameA(NULL, exePath, MAX_PATH);
	std::string dir(exePath);
	auto pos = dir.find_last_of("/\\");
	if (pos != std::string::npos)
		dir = dir.substr(0, pos);
	return dir + "\\daedalus_cache.ini";
}

std::string OffsetCache::BuildFingerprint()
{
	char exePath[MAX_PATH];
	GetModuleFileNameA(NULL, exePath, MAX_PATH);

	HANDLE hFile = CreateFileA(exePath, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
	if (hFile == INVALID_HANDLE_VALUE)
		return "";

	LARGE_INTEGER fileSize;
	GetFileSizeEx(hFile, &fileSize);

	FILETIME ftWrite;
	GetFileTime(hFile, NULL, NULL, &ftWrite);
	CloseHandle(hFile);

	std::ostringstream oss;
	oss << fileSize.QuadPart << "_" << ftWrite.dwHighDateTime << "_" << ftWrite.dwLowDateTime;
	return oss.str();
}

bool OffsetCache::Load()
{
	s_Entries.clear();
	s_Valid = false;

	HMODULE hGame = GetModuleHandleA(NULL);
	s_BaseAddress = (DWORD64)hGame;

	s_Fingerprint = BuildFingerprint();
	if (s_Fingerprint.empty())
	{
		Log::Warn("[OffsetCache] Could not build exe fingerprint");
		return false;
	}

	std::string cachePath = GetCachePath();
	std::ifstream file(cachePath);
	if (!file.is_open())
	{
		Log::Info("[OffsetCache] No cache file found - will create after resolution");
		return false;
	}

	std::string line;

	// First line: fingerprint
	if (!std::getline(file, line))
		return false;

	// Parse fingerprint line: "fingerprint=XXXXX"
	auto eqPos = line.find('=');
	if (eqPos == std::string::npos)
		return false;

	std::string storedFP = line.substr(eqPos + 1);
	if (storedFP != s_Fingerprint)
	{
		Log::Info("[OffsetCache] Exe changed (fingerprint mismatch) - cache invalidated");
		file.close();
		return false;
	}

	// Read entries: "symbolname=RVA,source"
	int count = 0;
	while (std::getline(file, line))
	{
		if (line.empty() || line[0] == '#' || line[0] == ';')
			continue;

		eqPos = line.find('=');
		if (eqPos == std::string::npos)
			continue;

		std::string name = line.substr(0, eqPos);
		std::string value = line.substr(eqPos + 1);

		auto commaPos = value.find(',');
		if (commaPos == std::string::npos)
			continue;

		CacheEntry entry;
		entry.rva = std::stoull(value.substr(0, commaPos), nullptr, 16);
		entry.source = value.substr(commaPos + 1);
		entry.address = s_BaseAddress + entry.rva;

		s_Entries[name] = entry;
		count++;
	}

	file.close();
	s_Valid = (count > 0);

	if (s_Valid)
		Log::Info("[OffsetCache] Loaded %d cached offsets (exe unchanged)", count);

	return s_Valid;
}

void OffsetCache::Save()
{
	if (s_Fingerprint.empty())
		s_Fingerprint = BuildFingerprint();

	if (s_Fingerprint.empty() || s_Entries.empty())
		return;

	std::string cachePath = GetCachePath();
	std::ofstream file(cachePath);
	if (!file.is_open())
	{
		Log::Warn("[OffsetCache] Could not write cache file: %s", cachePath.c_str());
		return;
	}

	file << "fingerprint=" << s_Fingerprint << "\n";
	file << "# Daedalus offset cache - auto-generated, do not edit\n";
	file << "# Format: symbol=RVA_hex,source\n";

	for (auto& pair : s_Entries)
	{
		std::ostringstream oss;
		oss << std::hex << pair.second.rva;
		file << pair.first << "=" << oss.str() << "," << pair.second.source << "\n";
	}

	file.close();
	Log::Info("[OffsetCache] Saved %d offsets to cache", (int)s_Entries.size());
}

DWORD64 OffsetCache::Get(const std::string& name)
{
	auto it = s_Entries.find(name);
	if (it != s_Entries.end())
		return it->second.address;
	return 0;
}

void OffsetCache::Put(const std::string& name, DWORD64 addr, const std::string& source)
{
	CacheEntry entry;
	entry.address = addr;
	entry.rva = addr - s_BaseAddress;
	entry.source = source;
	s_Entries[name] = entry;
}

std::string OffsetCache::GetSource(const std::string& name)
{
	auto it = s_Entries.find(name);
	if (it != s_Entries.end())
		return it->second.source;
	return "";
}
