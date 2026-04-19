#include "GameInfo.h"
#include <iostream>
#include "Utilities/Logger.h"
#include <string>
#include <filesystem>
#include <detours.h>
#include <dbghelp.h>
#include "INI.h"
#include "Utilities/Pattern.h"
#include "Utilities/Version.h"
#include "Cache/OffsetCache.h"
#include "../Hooks.h"
#include "../UE4/Ue4.hpp"

#pragma comment(lib, "dbghelp.lib")
#pragma execution_character_set("utf-8")

#define VALIDATE_PROFILE_DETOUR(fname, profileVar) \
    { \
        PVOID __fndPtr = nullptr; \
        /* Tier 1: Check cache */ \
        DWORD64 __cached = OffsetCache::Get(#fname); \
        if (__cached) \
        { \
            __fndPtr = (PVOID)__cached; \
            Log::Info("Found %s [cache]: 0x%p", #fname, __fndPtr); \
        } \
        /* Tier 2: DetourFindFunction (PDB) */ \
        if (!__fndPtr) \
        { \
            __fndPtr = DetourFindFunction(GAME_EXECUTABLE_NAME, #fname); \
            if (__fndPtr) \
            { \
                Log::Info("Found %s [PDB]: 0x%p", #fname, __fndPtr); \
                OffsetCache::Put(#fname, (DWORD64)__fndPtr, "pdb"); \
            } \
        } \
        /* Tier 3: DbgHelp SymFromName fallback */ \
        if (!__fndPtr && g_DbgHelpReady) \
        { \
            char __symBuf[sizeof(SYMBOL_INFO) + MAX_SYM_NAME]; \
            SYMBOL_INFO* __sym = (SYMBOL_INFO*)__symBuf; \
            memset(__symBuf, 0, sizeof(__symBuf)); \
            __sym->SizeOfStruct = sizeof(SYMBOL_INFO); \
            __sym->MaxNameLen = MAX_SYM_NAME; \
            if (SymFromName(GetCurrentProcess(), #fname, __sym)) \
            { \
                __fndPtr = (PVOID)__sym->Address; \
                Log::Info("Found %s [DbgHelp]: 0x%p", #fname, __fndPtr); \
                OffsetCache::Put(#fname, (DWORD64)__fndPtr, "dbghelp"); \
            } \
        } \
        if (__fndPtr) \
        { \
            GameProfile::SelectedGameProfile.profileVar = (DWORD64)__fndPtr; \
        } \
        else \
        { \
            Log::Error("Failed to locate definition for " #fname " - mods may be unstable."); \
        } \
    }

GameProfile GameProfile::SelectedGameProfile;
static bool g_DbgHelpReady = false;

void PrintLogo()
{
        const char* logo =
        " ██████╗░░█████╗░███████╗██████╗░░█████╗░██╗░░░░░██╗░░░██╗░██████╗ \n"
        " ██╔══██╗██╔══██╗██╔════╝██╔══██╗██╔══██╗██║░░░░░██║░░░██║██╔════╝ \n"
        " ██║░░██║███████║█████╗░░██║░░██║███████║██║░░░░░██║░░░██║╚█████╗░ \n"
        " ██║░░██║██╔══██║██╔══╝░░██║░░██║██╔══██║██║░░░░░██║░░░██║░╚═══██╗ \n"
        " ██████╔╝██║░░██║███████╗██████╔╝██║░░██║███████╗╚██████╔╝██████╔╝ \n"
        " ╚═════╝░╚═╝░░╚═╝╚══════╝╚═════╝░╚═╝░░╚═╝╚══════╝░╚═════╝░╚═════╝░ \n";

    std::cout << logo;
}

DWORD StringToDWord(std::string str)
{
    unsigned int m_dwIP;
    std::istringstream ss(&str[2]);
    ss >> std::hex >> m_dwIP;
    return m_dwIP;
}

std::string GetModuleFilePath(HMODULE hModule)
{
    std::string ModuleName = "";
    char szFileName[MAX_PATH] = { 0 };

    if (GetModuleFileNameA(hModule, szFileName, MAX_PATH))
        ModuleName = szFileName;

    return ModuleName;
}

void SetupProfile()
{
    char game_c[MAX_PATH];
    GetModuleFileNameA(NULL, game_c, MAX_PATH);
    std::string gamename = std::string(game_c);
    gamename = gamename.substr(0, gamename.find_last_of("."));
    gamename = gamename.substr(gamename.find_last_of("/\\"));
    gamename = gamename.substr(1);

    //Output File Initialization

    ShowWindow(GetConsoleWindow(), SW_SHOW);
    FreeConsole();
    AllocConsole();
    SetConsoleOutputCP(65001);

#pragma warning(push)
#pragma warning(disable:6031)
    freopen("CONIN$", "r", stdin);
    freopen("CONOUT$", "w", stdout);
    freopen("CONOUT$", "w", stderr);
#pragma warning(pop)

    PrintLogo();
    Log::Info("Daedalus Mod Loader - Release V %s", MODLOADER_VERSION);
    Log::Info("Adapted by edmiester777 for use with Icarus");
    

    std::string currentPath = std::filesystem::current_path().string();
    std::filesystem::path newPdbPath = std::filesystem::current_path() / "Icarus-Win64-Shipping.pdb";
    std::filesystem::path pdbPath = std::filesystem::current_path() / "Icarus" / "Binaries" / "Win64" / "Icarus-Win64-Shipping.pdb";
    if (currentPath.find("Win32") && !std::filesystem::exists(newPdbPath))
    {
        // PDB is not in a readable location for this executable. Making a copy
        std::error_code e;
        Log::Info("Executable directory does not contain valid PDB. Attempting to make symlink...");
        std::filesystem::copy_options opts =
            std::filesystem::copy_options::create_symlinks;
        std::filesystem::copy_file(
            pdbPath,
            newPdbPath,
            opts,
            e
        );

        if (e)
        {
            Log::Warn("Failed to copy symbols to current directory. Mods may be unstable.");
        }
    }

    GameProfile::SelectedGameProfile.ProfileName = gamename;
    Log::Info("Profile Detected: %s", gamename.c_str());
    std::ifstream file("Profile");

    // icarus definitions - referenced UnrealDumper

    GameProfile::SelectedGameProfile.IsUObjectMissing = false;
    GameProfile::SelectedGameProfile.defs.UObject.Index = 0xC;
    GameProfile::SelectedGameProfile.defs.UObject.Class = 0x10;
    GameProfile::SelectedGameProfile.defs.UObject.Name = 0x18;
    GameProfile::SelectedGameProfile.defs.UObject.Outer = 0x20;

    GameProfile::SelectedGameProfile.IsUStructMissing = false;
    GameProfile::SelectedGameProfile.defs.UStruct.SuperStruct = 0x40;
    GameProfile::SelectedGameProfile.defs.UStruct.Children = 0x48;
    GameProfile::SelectedGameProfile.defs.UStruct.PropertiesSize = 0x58;

    GameProfile::SelectedGameProfile.IsUFieldMissing = false;
    GameProfile::SelectedGameProfile.defs.UField.Next = 0x28;
    
    GameProfile::SelectedGameProfile.IsPropertyMissing = false;
    GameProfile::SelectedGameProfile.defs.Property.ArrayDim = 0x38;
    GameProfile::SelectedGameProfile.defs.Property.Offset = 0x4C;

    GameProfile::SelectedGameProfile.IsUFunctionMissing = false;
    GameProfile::SelectedGameProfile.defs.UFunction.FunctionFlags = 0xB0;
    GameProfile::SelectedGameProfile.defs.UFunction.Func = 0xB0 + 0x28;

    GameProfile::SelectedGameProfile.UsesFNamePool = true;

    // Load offset cache (invalidates automatically when exe changes)
    OffsetCache::Load();

    // FNamePool - check cache first, fall back to pattern scan
    DWORD64 cachedGName = OffsetCache::Get("FNamePool");
    if (cachedGName)
    {
        GameProfile::SelectedGameProfile.GName = cachedGName;
        Log::Info("FoundNamePool [cache]: 0x%p", cachedGName);
    }
    else
    {
        auto FPoolPat = Pattern::Find("74 09 48 8D 15 ? ? ? ? EB 16");
        if (FPoolPat != nullptr)
        {
            auto FPoolPatoffset = *reinterpret_cast<uint32_t*>(FPoolPat + 5);
            GameProfile::SelectedGameProfile.GName = (DWORD64)(FPoolPat + 9 + FPoolPatoffset);
            Log::Info("FoundNamePool [pattern]: 0x%p", GameProfile::SelectedGameProfile.GName);
            OffsetCache::Put("FNamePool", GameProfile::SelectedGameProfile.GName, "pattern");
        }
        else
        {
            Log::Error("GName Could Not Be Found!");
        }
    }

    // Initialize DbgHelp for symbol lookup fallback
    {
        SymSetOptions(SYMOPT_UNDNAME | SYMOPT_DEFERRED_LOADS);
        // Build search path: current dir + exe directory for PDB resolution
        std::string symSearchPath = std::filesystem::current_path().string();
        {
            char __exePath[MAX_PATH];
            GetModuleFileNameA(GetModuleHandleA(GAME_EXECUTABLE_NAME), __exePath, MAX_PATH);
            std::string exeDir(__exePath);
            auto lastSlash = exeDir.find_last_of("/\\");
            if (lastSlash != std::string::npos) exeDir = exeDir.substr(0, lastSlash);
            symSearchPath += ";" + exeDir;
        }
        if (SymInitialize(GetCurrentProcess(), symSearchPath.c_str(), FALSE))
        {
            HMODULE hGame = GetModuleHandleA(GAME_EXECUTABLE_NAME);
            if (hGame)
            {
                char exePath[MAX_PATH];
                GetModuleFileNameA(hGame, exePath, MAX_PATH);
                DWORD64 baseAddr = SymLoadModuleEx(GetCurrentProcess(), NULL, exePath, NULL, (DWORD64)hGame, 0, NULL, 0);
                if (baseAddr || GetLastError() == ERROR_SUCCESS)
                {
                    g_DbgHelpReady = true;
                    Log::Info("DbgHelp fallback initialized (base=0x%p)", (void*)hGame);
                }
                else
                    Log::Warn("DbgHelp SymLoadModuleEx failed (err=%d)", GetLastError());
            }
        }
        else
            Log::Warn("DbgHelp SymInitialize failed (err=%d)", GetLastError());
    }
    VALIDATE_PROFILE_DETOUR(GCoreObjectArrayForDebugVisualizers, GObject);
    VALIDATE_PROFILE_DETOUR(GWorld, GWorld);
    VALIDATE_PROFILE_DETOUR(AGameModeBase::InitGameState, GameStateInit);
    VALIDATE_PROFILE_DETOUR(AActor::BeginPlay, BeginPlay);
    VALIDATE_PROFILE_DETOUR(StaticLoadObject, StaticLoadObject);
    VALIDATE_PROFILE_DETOUR(UWorld::SpawnActor, SpawnActorFTrans);
    VALIDATE_PROFILE_DETOUR(UObject::CallFunctionByNameWithArguments, CallFunctionByNameWithArguments);
    VALIDATE_PROFILE_DETOUR(UObject::ProcessEvent, ProcessEvent);
    VALIDATE_PROFILE_DETOUR(UObject::ProcessInternal, ProcessInternals);
    VALIDATE_PROFILE_DETOUR(UClass::CreateDefaultObject, CreateDefaultObject);
    VALIDATE_PROFILE_DETOUR(StaticConstructObject_Internal, StaticConstructObject_Internal);

    GameProfile::SelectedGameProfile.IsUsingUpdatedStaticConstruct = true;

    // Save all resolved offsets to cache for next launch
    OffsetCache::Save();

    Hooks::SetupHooks();
}

void GameProfile::CreateGameProfile()
{
    //auto Module = GetModuleHandleA("DaedalusLoader.dll");
    //std::string path = GetModuleFilePath(Module);
    //path = path.substr(0, path.find_last_of("/\\"));
    SetupProfile();
}
