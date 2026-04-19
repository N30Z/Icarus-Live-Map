#pragma once
#include <string>
#include <unordered_map>
#include <windows.h>
#include "../UMLDefs.h"

// ============================================================================
// OffsetCache - Persistent symbol resolution cache
//
// Stores resolved symbol RVAs in daedalus_cache.ini next to the game exe.
// Uses exe fingerprint (size + timestamp) to auto-invalidate when the game
// updates. This means second launch skips all PDB/pattern scanning.
// ============================================================================

class LOADER_API OffsetCache
{
public:
	struct CacheEntry
	{
		DWORD64 address;       // Absolute address (base + RVA)
		DWORD64 rva;           // Relative virtual address
		std::string source;    // "cache", "pdb", "dbghelp", or "pattern"
	};

	// Load cache from disk. Returns true if fingerprint matches (cache valid).
	static bool Load();

	// Save all cached entries to disk.
	static void Save();

	// Get cached absolute address for a symbol. Returns 0 if not cached.
	static DWORD64 Get(const std::string& name);

	// Store a resolved address with its source tag.
	static void Put(const std::string& name, DWORD64 addr, const std::string& source);

	// Get the resolution source for a cached symbol.
	static std::string GetSource(const std::string& name);

	// Check if the cache has been loaded and is valid.
	static bool IsValid() { return s_Valid; }

private:
	// Build fingerprint string from exe size + timestamp
	static std::string BuildFingerprint();

	// Get the cache file path (next to game exe)
	static std::string GetCachePath();

	static std::unordered_map<std::string, CacheEntry> s_Entries;
	static std::string s_Fingerprint;
	static DWORD64 s_BaseAddress;
	static bool s_Valid;
};
