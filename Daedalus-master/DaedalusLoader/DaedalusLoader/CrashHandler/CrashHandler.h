#pragma once
#include <windows.h>
#include <string>
#include "../UMLDefs.h"

// ============================================================================
// CrashHandler - Minidump generation on unhandled exceptions
//
// Registers a vectored exception handler that writes a .dmp file to the
// Daedalus logs folder when the game crashes. The dump includes thread
// context, module info, and memory around the faulting address.
// ============================================================================

class LOADER_API CrashHandler
{
public:
	// Install the crash handler (call once at startup)
	static void Install();

	// Uninstall the crash handler
	static void Uninstall();

	// Check if handler is installed
	static bool IsInstalled() { return s_Handler != nullptr; }

	// Set to true when inside SEH-protected regions so the VEH skips logging
	static volatile bool s_InsideSEHProtection;

private:
	// The actual exception handler callback
	static LONG CALLBACK VectoredHandler(EXCEPTION_POINTERS* pExInfo);

	// Write minidump to disk
	static bool WriteMiniDump(EXCEPTION_POINTERS* pExInfo, const char* dumpPath);

	// Build a timestamped dump file path
	static std::string BuildDumpPath();

	// Get human-readable exception code name
	static const char* ExceptionCodeToString(DWORD code);

	static PVOID s_Handler;
};
