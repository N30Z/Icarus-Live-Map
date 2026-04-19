#include "CrashHandler.h"
#include "../Utilities/Logger.h"
#include <dbghelp.h>
#include <string>
#include <ctime>
#include <filesystem>
#include <sstream>
#include <iomanip>

#pragma comment(lib, "dbghelp.lib")

PVOID CrashHandler::s_Handler = nullptr;
volatile bool CrashHandler::s_InsideSEHProtection = false;

void CrashHandler::Install()
{
	if (s_Handler)
		return;

	s_Handler = AddVectoredExceptionHandler(1, VectoredHandler);
	if (s_Handler)
		Log::Info("[CrashHandler] Installed - minidumps will be saved on crash");
	else
		Log::Warn("[CrashHandler] Failed to install vectored exception handler");
}

void CrashHandler::Uninstall()
{
	if (s_Handler)
	{
		RemoveVectoredExceptionHandler(s_Handler);
		s_Handler = nullptr;
	}
}

LONG CALLBACK CrashHandler::VectoredHandler(EXCEPTION_POINTERS* pExInfo)
{
	// If we're inside an SEH-protected region, skip logging — SEH will handle it
	if (s_InsideSEHProtection)
		return EXCEPTION_CONTINUE_SEARCH;

	DWORD code = pExInfo->ExceptionRecord->ExceptionCode;

	// Only handle fatal exceptions, let non-fatal ones pass through
	switch (code)
	{
	case EXCEPTION_ACCESS_VIOLATION:
	case EXCEPTION_STACK_OVERFLOW:
	case EXCEPTION_ARRAY_BOUNDS_EXCEEDED:
	case EXCEPTION_ILLEGAL_INSTRUCTION:
	case EXCEPTION_INT_DIVIDE_BY_ZERO:
	case EXCEPTION_PRIV_INSTRUCTION:
	case EXCEPTION_GUARD_PAGE:
	case EXCEPTION_NONCONTINUABLE_EXCEPTION:
		break; // Handle these
	default:
		return EXCEPTION_CONTINUE_SEARCH; // Ignore C++ exceptions, breakpoints, etc.
	}

	// Log crash info
	Log::Error("=== CRASH DETECTED ===");
	Log::Error("Exception: %s (0x%08X)", ExceptionCodeToString(code), code);
	Log::Error("Address: 0x%p", pExInfo->ExceptionRecord->ExceptionAddress);

	if (code == EXCEPTION_ACCESS_VIOLATION && pExInfo->ExceptionRecord->NumberParameters >= 2)
	{
		const char* accessType = (pExInfo->ExceptionRecord->ExceptionInformation[0] == 0) ? "reading" : "writing";
		Log::Error("Attempted %s address: 0x%p", accessType, (void*)pExInfo->ExceptionRecord->ExceptionInformation[1]);
	}

	// Log register state
	CONTEXT* ctx = pExInfo->ContextRecord;
	Log::Error("RIP=0x%p RSP=0x%p RBP=0x%p", (void*)ctx->Rip, (void*)ctx->Rsp, (void*)ctx->Rbp);
	Log::Error("RAX=0x%p RBX=0x%p RCX=0x%p RDX=0x%p", (void*)ctx->Rax, (void*)ctx->Rbx, (void*)ctx->Rcx, (void*)ctx->Rdx);

	// Try to identify which module the crash is in
	HMODULE hMod = NULL;
	if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
		(LPCSTR)pExInfo->ExceptionRecord->ExceptionAddress, &hMod))
	{
		char modName[MAX_PATH];
		GetModuleFileNameA(hMod, modName, MAX_PATH);
		DWORD64 offset = (DWORD64)pExInfo->ExceptionRecord->ExceptionAddress - (DWORD64)hMod;
		Log::Error("Crash in module: %s + 0x%llX", modName, offset);
	}

	// Write minidump
	std::string dumpPath = BuildDumpPath();
	if (WriteMiniDump(pExInfo, dumpPath.c_str()))
	{
		Log::Error("Minidump saved: %s", dumpPath.c_str());
	}
	else
	{
		Log::Error("Failed to write minidump");
	}

	Log::Error("=== END CRASH REPORT ===");

	// Force flush the log
	Log::DumpLog();

	return EXCEPTION_CONTINUE_SEARCH;
}

bool CrashHandler::WriteMiniDump(EXCEPTION_POINTERS* pExInfo, const char* dumpPath)
{
	HANDLE hFile = CreateFileA(dumpPath, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
	if (hFile == INVALID_HANDLE_VALUE)
		return false;

	MINIDUMP_EXCEPTION_INFORMATION mei;
	mei.ThreadId = GetCurrentThreadId();
	mei.ExceptionPointers = pExInfo;
	mei.ClientPointers = FALSE;

	// Include thread info, module info, and memory near the crash
	MINIDUMP_TYPE dumpType = (MINIDUMP_TYPE)(
		MiniDumpNormal |
		MiniDumpWithThreadInfo |
		MiniDumpWithIndirectlyReferencedMemory |
		MiniDumpWithModuleHeaders
	);

	BOOL result = MiniDumpWriteDump(
		GetCurrentProcess(),
		GetCurrentProcessId(),
		hFile,
		dumpType,
		&mei,
		NULL,
		NULL
	);

	CloseHandle(hFile);
	return result != FALSE;
}

std::string CrashHandler::BuildDumpPath()
{
	// Save dumps to game dir / Daedalus_Crashes /
	char exePath[MAX_PATH];
	GetModuleFileNameA(NULL, exePath, MAX_PATH);
	std::string dir(exePath);
	auto pos = dir.find_last_of("/\\");
	if (pos != std::string::npos)
		dir = dir.substr(0, pos);

	std::string crashDir = dir + "\\Daedalus_Crashes";
	std::filesystem::create_directories(crashDir);

	// Timestamp the dump file
	time_t now = time(nullptr);
	struct tm local;
	localtime_s(&local, &now);

	std::ostringstream oss;
	oss << crashDir << "\\crash_"
		<< std::setfill('0')
		<< (1900 + local.tm_year)
		<< std::setw(2) << (1 + local.tm_mon)
		<< std::setw(2) << local.tm_mday << "_"
		<< std::setw(2) << local.tm_hour
		<< std::setw(2) << local.tm_min
		<< std::setw(2) << local.tm_sec
		<< ".dmp";

	return oss.str();
}

const char* CrashHandler::ExceptionCodeToString(DWORD code)
{
	switch (code)
	{
	case EXCEPTION_ACCESS_VIOLATION:         return "ACCESS_VIOLATION";
	case EXCEPTION_STACK_OVERFLOW:           return "STACK_OVERFLOW";
	case EXCEPTION_ARRAY_BOUNDS_EXCEEDED:    return "ARRAY_BOUNDS_EXCEEDED";
	case EXCEPTION_ILLEGAL_INSTRUCTION:      return "ILLEGAL_INSTRUCTION";
	case EXCEPTION_INT_DIVIDE_BY_ZERO:       return "INT_DIVIDE_BY_ZERO";
	case EXCEPTION_PRIV_INSTRUCTION:         return "PRIVILEGED_INSTRUCTION";
	case EXCEPTION_GUARD_PAGE:               return "GUARD_PAGE";
	case EXCEPTION_NONCONTINUABLE_EXCEPTION: return "NONCONTINUABLE";
	default:                                 return "UNKNOWN";
	}
}
