#include <windows.h>
#include <dbghelp.h>
#include <stdio.h>
#pragma comment(lib, "dbghelp.lib")

int main() {
    const char* symbols[] = {
        "UObject::ProcessEvent",
        "GWorld",
        "GCoreObjectArrayForDebugVisualizers",
        "AActor::BeginPlay",
        "AGameModeBase::InitGameState",
        "StaticLoadObject",
        "UWorld::SpawnActor",
        "StaticConstructObject_Internal",
        NULL
    };

    HMODULE hGame = LoadLibraryExA(
        "E:\\SteamLibrary\\steamapps\\common\\Icarus\\Icarus\\Binaries\\Win64\\Icarus-Win64-Shipping.exe",
        NULL, DONT_RESOLVE_DLL_REFERENCES);
    if (!hGame) { printf("Failed to load exe: %d\n", GetLastError()); return 1; }
    printf("Loaded exe at: 0x%p\n", hGame);

    SymSetOptions(SYMOPT_UNDNAME | SYMOPT_DEFERRED_LOADS | SYMOPT_DEBUG);
    if (!SymInitialize(GetCurrentProcess(), "E:\\SteamLibrary\\steamapps\\common\\Icarus\\Icarus\\Binaries\\Win64", FALSE)) {
        printf("SymInitialize failed: %d\n", GetLastError());
        return 1;
    }
    printf("SymInitialize OK\n");

    DWORD64 base = SymLoadModuleEx(GetCurrentProcess(), NULL,
        "E:\\SteamLibrary\\steamapps\\common\\Icarus\\Icarus\\Binaries\\Win64\\Icarus-Win64-Shipping.exe",
        NULL, (DWORD64)hGame, 0, NULL, 0);
    printf("SymLoadModuleEx: base=0x%llx err=%d\n", base, GetLastError());

    char buf[sizeof(SYMBOL_INFO) + MAX_SYM_NAME];
    SYMBOL_INFO* sym = (SYMBOL_INFO*)buf;
    
    for (int i = 0; symbols[i]; i++) {
        memset(buf, 0, sizeof(buf));
        sym->SizeOfStruct = sizeof(SYMBOL_INFO);
        sym->MaxNameLen = MAX_SYM_NAME;
        
        if (SymFromName(GetCurrentProcess(), symbols[i], sym)) {
            printf("FOUND: %s -> 0x%llx\n", symbols[i], sym->Address);
        } else {
            printf("NOT FOUND: %s (err=%d)\n", symbols[i], GetLastError());
        }
    }

    // Also try enumerating some symbols to see what IS in the PDB
    printf("\n--- Sample symbols containing 'ProcessEvent' ---\n");
    SymEnumSymbols(GetCurrentProcess(), (DWORD64)hGame, "*ProcessEvent*", 
        [](PSYMBOL_INFO pSymInfo, ULONG SymbolSize, PVOID UserContext) -> BOOL {
            printf("  %s @ 0x%llx\n", pSymInfo->Name, pSymInfo->Address);
            return TRUE;
        }, NULL);

    printf("\n--- Sample symbols containing 'BeginPlay' ---\n");
    SymEnumSymbols(GetCurrentProcess(), (DWORD64)hGame, "*BeginPlay*",
        [](PSYMBOL_INFO pSymInfo, ULONG SymbolSize, PVOID UserContext) -> BOOL {
            printf("  %s @ 0x%llx\n", pSymInfo->Name, pSymInfo->Address);
            return TRUE;
        }, NULL);

    SymCleanup(GetCurrentProcess());
    FreeLibrary(hGame);
    return 0;
}
