/**
 * LiveMapMod.cpp  –  DaedalusLoader CoreMod
 *
 * Subklasse von Mod (DaedalusLoader.dll).
 * DaedalusLoader lädt diese DLL, woraufhin ein Thread alle 2 s GWorld scannt
 * und Spielerdaten entweder per HTTP POST an server.py schickt (Docker/Wine)
 * oder als live_players.json schreibt (Windows-native).
 *
 * Konfiguration: LiveMapMod.ini (neben der DLL)
 *   [Config]
 *   IntervalMs=2000
 *   ServerUrl=http://192.168.1.x:9090    ; aktiviert HTTP-POST statt Datei
 */

#define WIN32_LEAN_AND_MEAN
#include <Windows.h>
#include <Psapi.h>
#include <Winhttp.h>
#include <cstdint>
#include <cmath>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>
#include "Mod.h"

#pragma comment(lib, "Psapi.lib")
#pragma comment(lib, "DaedalusLoader.lib")
#pragma comment(lib, "Winhttp.lib")

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

static wchar_t g_OutputPath[MAX_PATH] = {};
static wchar_t g_ServerUrl[512]       = {};   // http://host:port  → HTTP POST mode
static DWORD   g_IntervalMs           = 2000;
static HMODULE g_hSelf                = nullptr;

static void LoadConfig()
{
    // Derive DLL directory at runtime — works in Wine/Docker without hardcoded paths.
    wchar_t dll_path[MAX_PATH];
    GetModuleFileNameW(g_hSelf, dll_path, MAX_PATH);

    // Default output: <dll_dir>\live_players.json
    wcscpy_s(g_OutputPath, dll_path);
    wchar_t* sep = wcsrchr(g_OutputPath, L'\\');
    if (!sep) sep = wcsrchr(g_OutputPath, L'/');
    if (sep) wcscpy_s(sep + 1, MAX_PATH - (sep + 1 - g_OutputPath), L"live_players.json");

    // INI: <dll_dir>\LiveMapMod.ini
    wchar_t ini[MAX_PATH];
    wcscpy_s(ini, dll_path);
    wchar_t* dot = wcsrchr(ini, L'.');
    if (dot) wcscpy_s(dot, 5, L".ini");

    GetPrivateProfileStringW(L"Config", L"OutputPath",
        g_OutputPath, g_OutputPath, MAX_PATH, ini);
    GetPrivateProfileStringW(L"Config", L"ServerUrl",
        L"", g_ServerUrl, 512, ini);
    g_IntervalMs = GetPrivateProfileIntW(L"Config", L"IntervalMs",
        g_IntervalMs, ini);
}

// ---------------------------------------------------------------------------
// Debug-Log (neben OutputPath)
// ---------------------------------------------------------------------------

static void DebugLog(const char* msg)
{
    wchar_t logPath[MAX_PATH];
    wcscpy_s(logPath, g_OutputPath);
    wchar_t* dot = wcsrchr(logPath, L'.');
    if (dot) wcscpy_s(dot, 9, L"_mod.log");

    // Win32-only: kein CRT nötig, sicher aus jedem Kontext
    HANDLE hFile = CreateFileW(logPath, GENERIC_WRITE, FILE_SHARE_READ,
                               nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (hFile == INVALID_HANDLE_VALUE) return;
    SetFilePointer(hFile, 0, nullptr, FILE_END);

    SYSTEMTIME st{};
    GetLocalTime(&st);
    char line[256];
    int len = sprintf_s(line, "%02d:%02d:%02d %s\r\n",
                        st.wHour, st.wMinute, st.wSecond, msg);
    DWORD written;
    WriteFile(hFile, line, len, &written, nullptr);
    CloseHandle(hFile);
}

// ---------------------------------------------------------------------------
// HTTP POST (WinHTTP) — used when ServerUrl is configured (Docker/Wine)
// ---------------------------------------------------------------------------

static void PostJson(const std::string& body)
{
    if (!g_ServerUrl[0]) return;

    URL_COMPONENTSW uc{};
    uc.dwStructSize     = sizeof(uc);
    wchar_t host[256]{}, path[512]{};
    uc.lpszHostName     = host;
    uc.dwHostNameLength = 256;
    uc.lpszUrlPath      = path;
    uc.dwUrlPathLength  = 512;
    if (!WinHttpCrackUrl(g_ServerUrl, 0, 0, &uc)) return;

    HINTERNET hSess = WinHttpOpen(L"LiveMapMod/1.0",
        WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!hSess) return;
    WinHttpSetTimeouts(hSess, 2000, 2000, 2000, 2000);

    HINTERNET hConn = WinHttpConnect(hSess, host, uc.nPort, 0);
    if (!hConn) { WinHttpCloseHandle(hSess); return; }

    HINTERNET hReq = WinHttpOpenRequest(hConn, L"POST",
        path[0] ? path : L"/players",
        nullptr, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0);
    if (!hReq) { WinHttpCloseHandle(hConn); WinHttpCloseHandle(hSess); return; }

    DWORD sz = static_cast<DWORD>(body.size());
    WinHttpSendRequest(hReq,
        L"Content-Type: application/json\r\n", static_cast<DWORD>(-1),
        const_cast<char*>(body.c_str()), sz, sz, 0);
    WinHttpReceiveResponse(hReq, nullptr);

    WinHttpCloseHandle(hReq);
    WinHttpCloseHandle(hConn);
    WinHttpCloseHandle(hSess);
}

// ---------------------------------------------------------------------------
// UE4 offsets
// ---------------------------------------------------------------------------

static const uint32_t OFF_PLAYER_NAME    = 0x368;
static const uint32_t OFF_PAWN_PRIVATE   = 0x3A0;
static const uint32_t OFF_ROOT_COMPONENT = 0x198;
static const uint32_t OFF_REL_LOCATION   = 0x11C;

// ---------------------------------------------------------------------------
// SEH-sichere Speicher-Helfer (POD-only, kein RAII in __try)
// ---------------------------------------------------------------------------

static uintptr_t SafeReadPtr(uintptr_t addr)
{
    uintptr_t val = 0;
    __try { val = *reinterpret_cast<uintptr_t*>(addr); }
    __except (EXCEPTION_EXECUTE_HANDLER) {}
    return val;
}

static float SafeReadFloat(uintptr_t addr)
{
    float val = 0.f;
    __try { val = *reinterpret_cast<float*>(addr); }
    __except (EXCEPTION_EXECUTE_HANDLER) {}
    return val;
}

static bool SafeMemcpy(void* dst, uintptr_t src, size_t len)
{
    __try { memcpy(dst, reinterpret_cast<const void*>(src), len); return true; }
    __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}

static bool SafeReadInt32(uintptr_t addr, int32_t& out)
{
    __try { out = *reinterpret_cast<int32_t*>(addr); return true; }
    __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}

// ---------------------------------------------------------------------------
// FString lesen
// ---------------------------------------------------------------------------

static std::wstring ReadFString(uintptr_t addr)
{
    uintptr_t data_ptr = SafeReadPtr(addr);
    int32_t   length   = 0;
    if (!SafeReadInt32(addr + 8, length)) return {};
    if (!data_ptr || length <= 0 || length > 512) return {};

    std::wstring result(length, L'\0');
    if (!SafeMemcpy(&result[0], data_ptr, length * sizeof(wchar_t)))
        return {};
    if (!result.empty() && result.back() == L'\0')
        result.pop_back();
    return result;
}


// ---------------------------------------------------------------------------
// UObject-Validierung via vtable
// ---------------------------------------------------------------------------

static uintptr_t g_ModBase = 0;
static size_t    g_ModSize = 0;

// Offset cache – values from Python --trace (offsets.json), confirmed correct for Icarus build
// Forward-declared here so FindGWorldPtr can use g_OffPawn for prospect detection
static uint32_t g_OffPawn     = 0x30;
static uint32_t g_OffComp     = 0xA0;
static uint32_t g_OffLoc      = 0x78;
static bool     g_OffVerified = true;

static bool IsHeapUObject(uintptr_t addr)
{
    if (addr < 0x10000 || addr > 0x7FFFFFFFFFFF) return false;
    if (g_ModBase <= addr && addr < g_ModBase + g_ModSize) return false;
    uintptr_t vtable = SafeReadPtr(addr);
    return g_ModBase <= vtable && vtable < g_ModBase + g_ModSize;
}

// Scans PlayerState from offset 0x100 to 0x700 for valid UTF-16 FStrings. Runs once.
static void ScanPlayerStateForFStrings(uintptr_t ps)
{
    static bool s_done = false;
    if (s_done) return;
    s_done = true;

    char hdr[128];
    sprintf_s(hdr, "FString scan ps=0x%llX:", (unsigned long long)ps);
    DebugLog(hdr);

    for (uint32_t off = 0x100; off < 0x700; off += 8) {
        uintptr_t data_ptr = SafeReadPtr(ps + off);
        if (data_ptr < 0x10000 || data_ptr > 0x7FFFFFFFFFFF) continue;
        if (g_ModBase <= data_ptr && data_ptr < g_ModBase + g_ModSize) continue;

        int32_t length = 0;
        if (!SafeReadInt32(ps + off + 8, length)) continue;
        if (length <= 0 || length > 64) continue;

        wchar_t wbuf[65] = {};
        if (!SafeMemcpy(wbuf, data_ptr, length * sizeof(wchar_t))) continue;

        bool printable = true;
        for (int k = 0; k < length; ++k) {
            wchar_t c = wbuf[k];
            if (c != 0 && (c < 0x20 || c > 0x7E)) { printable = false; break; }
        }
        if (!printable) continue;

        char narrow[65] = {};
        for (int k = 0; k < length && k < 64; ++k) narrow[k] = (char)wbuf[k];

        char line[192];
        sprintf_s(line, "  +0x%X len=%d '%s'", off, length, narrow);
        DebugLog(line);
    }
    DebugLog("FString scan done.");
}

// ---------------------------------------------------------------------------
// GWorld-Pattern-Scan (RIP-relative MOV)
// ---------------------------------------------------------------------------

struct GWorldPattern { uint8_t bytes[3]; int rel_off; int instr_len; };
static const GWorldPattern PATTERNS[] = {
    {{0x48, 0x8B, 0x1D}, 3, 7},
    {{0x48, 0x8B, 0x05}, 3, 7},
    {{0x48, 0x8B, 0x0D}, 3, 7},
    {{0x48, 0x8B, 0x15}, 3, 7},
    {{0x4C, 0x8B, 0x05}, 3, 7},
};
static const uint16_t CTX[] = { 0x8548, 0x8348, 0x3B48 };

static uintptr_t FindGWorldPtr()
{
    // Enumerate readable committed regions within module (mirrors Python VirtualQueryEx approach).
    // The false-positive instruction lives in a non-committed page and is skipped this way.
    struct Region { uintptr_t base; size_t size; };
    Region regions[512];
    int nregions = 0;

    {
        MEMORY_BASIC_INFORMATION mbi{};
        uintptr_t addr    = g_ModBase;
        uintptr_t mod_end = g_ModBase + g_ModSize;
        while (addr < mod_end && nregions < 512) {
            if (!VirtualQuery(reinterpret_cast<LPCVOID>(addr), &mbi, sizeof(mbi))) {
                addr += 0x1000; continue;
            }
            uintptr_t reg_base = reinterpret_cast<uintptr_t>(mbi.BaseAddress);
            SIZE_T    reg_size = mbi.RegionSize;
            bool readable = (mbi.State == MEM_COMMIT) &&
                            !(mbi.Protect & PAGE_GUARD) &&
                            !(mbi.Protect & PAGE_NOACCESS) &&
                            (mbi.Protect & (PAGE_READONLY | PAGE_READWRITE |
                                            PAGE_WRITECOPY | PAGE_EXECUTE_READ |
                                            PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY));
            if (readable) {
                uintptr_t r0 = (reg_base < g_ModBase) ? g_ModBase : reg_base;
                uintptr_t r1 = (reg_base + reg_size > mod_end) ? mod_end : reg_base + reg_size;
                if (r1 > r0) regions[nregions++] = {r0, r1 - r0};
            }
            uintptr_t next = reg_base + reg_size;
            if (next <= addr) break;
            addr = next;
        }
    }

    {
        char rbuf[64];
        sprintf_s(rbuf, "GWorld scan: %d readable regions", nregions);
        DebugLog(rbuf);
    }

    int hits = 0, ctx_fail = 0, range_fail = 0, modval_fail = 0;
    uintptr_t best_candidate = 0;
    int       best_pa_cnt    = -1;
    int       best_vpawn     = -1;  // players with valid pawn at g_OffPawn → prefer prospect over lobby

    for (auto& p : PATTERNS) {
        for (int r = 0; r < nregions; ++r) {
            auto*    data  = reinterpret_cast<uint8_t*>(regions[r].base);
            size_t   rsz   = regions[r].size;
            uintptr_t rbase = regions[r].base;

            for (size_t i = 0; i + (size_t)p.instr_len + 8 < rsz; ++i) {
                if (data[i]   != p.bytes[0] ||
                    data[i+1] != p.bytes[1] ||
                    data[i+2] != p.bytes[2]) continue;
                ++hits;

                uint16_t ctx_word = *reinterpret_cast<uint16_t*>(data + i + p.instr_len);
                bool ctx_ok = false;
                for (uint16_t c : CTX) if (ctx_word == c) { ctx_ok = true; break; }
                if (!ctx_ok) { ++ctx_fail; continue; }

                int32_t   rel32      = *reinterpret_cast<int32_t*>(data + i + p.rel_off);
                uintptr_t instr_va   = rbase + i;
                uintptr_t candidate  = instr_va + (uintptr_t)p.instr_len + (int64_t)rel32;

                uintptr_t val = SafeReadPtr(candidate);
                if (val < 0x10000 || val > 0x7FFFFFFFFFFF) { ++range_fail; continue; }
                if (g_ModBase <= val && val < g_ModBase + g_ModSize) { ++modval_fail; continue; }

                uintptr_t vtable = SafeReadPtr(val);
                if (!(g_ModBase <= vtable && vtable < g_ModBase + g_ModSize)) continue;

                // Real UWorld has many UObject pointers; false positives have none
                int uobj_count = 0;
                for (uint32_t vo = 0; vo < 0x200 && uobj_count < 3; vo += 8)
                    if (IsHeapUObject(SafeReadPtr(val + vo))) ++uobj_count;
                if (uobj_count < 3) continue;

                // Python-confirmed: real UWorld always has GameState (UObject) at +0x038.
                uintptr_t gs_check = SafeReadPtr(val + 0x038);
                if (!IsHeapUObject(gs_check)) {
                    char reject[160];
                    sprintf_s(reject, "GWorld reject (no GS at +0x38): val=0x%llX gs=0x%llX",
                              (unsigned long long)val, (unsigned long long)gs_check);
                    DebugLog(reject);
                    continue;
                }

                // Python-confirmed: real GameState has PlayerArray (TArray<APlayerState*>) at +0x090.
                // Data pointer must be a heap address; if any players connected, first element is UObject.
                uintptr_t pa_data = SafeReadPtr(gs_check + 0x090);
                int32_t   pa_cnt  = 0;
                SafeReadInt32(gs_check + 0x098, pa_cnt);
                bool pa_ok = (pa_data > 0x10000 && pa_data <= 0x7FFFFFFFFFFF &&
                              !(g_ModBase <= pa_data && pa_data < g_ModBase + g_ModSize) &&
                              pa_cnt >= 0 && pa_cnt <= 64);
                if (pa_ok && pa_cnt > 0)
                    pa_ok = IsHeapUObject(SafeReadPtr(pa_data));
                if (!pa_ok) {
                    char reject[160];
                    sprintf_s(reject, "GWorld reject (bad PlayerArray at GS+0x90): val=0x%llX pa_data=0x%llX cnt=%d",
                              (unsigned long long)val, (unsigned long long)pa_data, pa_cnt);
                    DebugLog(reject);
                    continue;
                }

                // Count PlayerStates with valid pawn at g_OffPawn.
                // Prospect worlds have real pawns; lobby/streaming worlds have none.
                // This ensures we pick the active prospect over the lobby (which has more entries).
                int vpawn_cnt = 0;
                for (int pi = 0; pi < pa_cnt && pi < 32; ++pi) {
                    uintptr_t ps_i = SafeReadPtr(pa_data + pi * 8);
                    if (IsHeapUObject(ps_i) && IsHeapUObject(SafeReadPtr(ps_i + g_OffPawn)))
                        ++vpawn_cnt;
                }

                {
                    char buf[192];
                    sprintf_s(buf, "GWorld cand: candidate=0x%llX gs=0x%llX pa_cnt=%d vpawn=%d",
                              (unsigned long long)candidate,
                              (unsigned long long)gs_check, pa_cnt, vpawn_cnt);
                    DebugLog(buf);
                }
                if (vpawn_cnt > best_vpawn ||
                    (vpawn_cnt == best_vpawn && pa_cnt > best_pa_cnt)) {
                    best_vpawn     = vpawn_cnt;
                    best_pa_cnt    = pa_cnt;
                    best_candidate = candidate;
                }
            }
        }
    }

    if (best_candidate) {
        char buf[128];
        sprintf_s(buf, "GWorld best: candidate=0x%llX pa_cnt=%d vpawn=%d",
                  (unsigned long long)best_candidate, best_pa_cnt, best_vpawn);
        DebugLog(buf);
        return best_candidate;
    }

    char diag[256];
    sprintf_s(diag, "GWorld scan failed: hits=%d ctx_fail=%d range_fail=%d modval_fail=%d regions=%d",
              hits, ctx_fail, range_fail, modval_fail, nregions);
    DebugLog(diag);
    return 0;
}

// ---------------------------------------------------------------------------
// Spieler lesen
// ---------------------------------------------------------------------------

struct PlayerInfo { std::wstring name; std::string steam_id; bool online; float x_m, y_m, z_m; };

// Plausible Icarus world coordinate: 1 m..5000 m from origin, no NaN/Inf
static bool IsPlausibleCoord(float x, float y, float z)
{
    if (!isfinite(x) || !isfinite(y) || !isfinite(z)) return false;
    float m = fabsf(x);
    if (fabsf(y) > m) m = fabsf(y);
    if (fabsf(z) > m) m = fabsf(z);
    return m >= 100.f && m <= 500000.f;
}


// Heuristic scan: PlayerState → Pawn (UObject) → Component (UObject) → FVector
// Same logic as Python's find_player_location / trace_pointer_chain.
// Runs once; updates g_Off* globals.
static bool AutoDiscoverOffsets(uintptr_t ps)
{
    // Scan ranges must cover both UE4 defaults (0x3A0, 0x198, 0x11C)
    // and any Icarus-specific offsets found by offsets.json (0x30, 0xA0, 0x78).
    const uint32_t PS_SCAN   = 0xC00;  // covers 0x3A0
    const uint32_t PAWN_SCAN = 0x600;  // covers 0x198
    const uint32_t COMP_SCAN = 0x200;  // covers 0x11C

    uint8_t pawn_buf[0x600];
    uint8_t comp_buf[0x200];

    float    best_mag = 0.f;
    uint32_t best_po = 0, best_co = 0, best_lo = 0;

    for (uint32_t po = 0; po < PS_SCAN; po += 8) {
        uintptr_t pawn = SafeReadPtr(ps + po);
        if (!IsHeapUObject(pawn)) continue;
        if (!SafeMemcpy(pawn_buf, pawn, PAWN_SCAN)) continue;

        for (uint32_t co = 0; co + 8 <= PAWN_SCAN; co += 8) {
            uintptr_t comp;
            memcpy(&comp, pawn_buf + co, 8);
            if (!IsHeapUObject(comp)) continue;
            if (!SafeMemcpy(comp_buf, comp, COMP_SCAN)) continue;

            for (uint32_t lo = 0; lo + 12 <= COMP_SCAN; lo += 4) {
                float x, y, z;
                memcpy(&x, comp_buf + lo,     4);
                memcpy(&y, comp_buf + lo + 4, 4);
                memcpy(&z, comp_buf + lo + 8, 4);
                if (!IsPlausibleCoord(x, y, z)) continue;
                float mag = fabsf(x) + fabsf(y) + fabsf(z);
                if (mag > best_mag) {
                    best_mag = mag;
                    best_po = po; best_co = co; best_lo = lo;
                }
            }
        }
    }

    if (best_mag > 0.f) {
        g_OffPawn = best_po; g_OffComp = best_co; g_OffLoc = best_lo;
        g_OffVerified = true;
        char buf[128];
        sprintf_s(buf, "AutoOffsets: pawn=0x%X comp=0x%X loc=0x%X mag=%.0f",
                  best_po, best_co, best_lo, best_mag);
        DebugLog(buf);
        return true;
    }
    DebugLog("AutoOffsets: nichts gefunden");
    return false;
}

static bool FindPlayerArray(uintptr_t gs, uintptr_t& out_data, int32_t& out_count,
                             char* diag_buf, int diag_sz)
{
    int cnt_fail = 0, mx_fail = 0, dp_fail = 0, first_fail = 0;
    int best_cnt = -1; uintptr_t best_dp = 0; uint32_t best_ao = 0;

    for (uint32_t ao = 0; ao < 0x600 - 16; ao += 8) {
        uintptr_t dp  = SafeReadPtr(gs + ao);
        int32_t   cnt = 0, mx = 0;
        __try {
            cnt = *reinterpret_cast<int32_t*>(gs + ao + 8);
            mx  = *reinterpret_cast<int32_t*>(gs + ao + 12);
        }
        __except (EXCEPTION_EXECUTE_HANDLER) { continue; }

        // Track closest-looking candidate for diagnostics
        if (cnt > 0 && cnt <= 64 && mx >= cnt && mx <= 256 &&
            dp > 0x10000 && dp <= 0x7FFFFFFFFFFF &&
            !(g_ModBase <= dp && dp < g_ModBase + g_ModSize)) {
            if (cnt > best_cnt) { best_cnt = cnt; best_dp = dp; best_ao = ao; }
        }

        if (cnt < 1 || cnt > 32)  { ++cnt_fail; continue; }
        if (mx < cnt || mx > 128) { ++mx_fail;  continue; }
        if (dp < 0x10000 || dp > 0x7FFFFFFFFFFF) { ++dp_fail; continue; }
        if (g_ModBase <= dp && dp < g_ModBase + g_ModSize) { ++dp_fail; continue; }

        uintptr_t first = SafeReadPtr(dp);
        if (!IsHeapUObject(first)) { ++first_fail; continue; }

        out_data  = dp;
        out_count = cnt;
        if (diag_buf) sprintf_s(diag_buf, diag_sz,
            "PlayerArray at +0x%03X dp=0x%llX cnt=%d",
            ao, (unsigned long long)dp, cnt);
        return true;
    }

    if (diag_buf) {
        if (best_cnt > 0)
            sprintf_s(diag_buf, diag_sz,
                "no arr: cnt_fail=%d mx_fail=%d dp_fail=%d first_fail=%d | best: ao=0x%03X dp=0x%llX cnt=%d",
                cnt_fail, mx_fail, dp_fail, first_fail,
                best_ao, (unsigned long long)best_dp, best_cnt);
        else
            sprintf_s(diag_buf, diag_sz,
                "no arr: cnt_fail=%d mx_fail=%d dp_fail=%d first_fail=%d",
                cnt_fail, mx_fail, dp_fail, first_fail);
    }
    return false;
}

// ps+0x1B8 = FString travel URL, e.g. "/Game/Maps/...?Name=GD | Phoenix_?..."
// Returns the Steam name (after " | " if present, else full Name= value).
static std::wstring ReadSteamName(uintptr_t ps)
{
    std::wstring url = ReadFString(ps + 0x1B8);
    if (url.empty()) return {};
    auto p = url.find(L"?Name=");
    if (p == std::wstring::npos) return {};
    std::wstring val = url.substr(p + 6);
    auto q = val.find(L'?');
    if (q != std::wstring::npos) val = val.substr(0, q);
    auto pipe = val.find(L" | ");
    return (pipe != std::wstring::npos) ? val.substr(pipe + 3) : val;
}

// ps+0x168 → TSharedPtr<FUniqueNetId> wrapper → +0x18 = Steam ID uint64
static std::string ReadSteamId(uintptr_t ps)
{
    uintptr_t uid = SafeReadPtr(ps + 0x168);
    if (!uid) return {};
    uint64_t sid = SafeReadPtr(uid + 0x18);
    // Steam64 IDs start with 0x11000010...
    if (sid < 0x1100001000000000ULL || sid > 0x1200000000000000ULL) return {};
    char buf[24];
    sprintf_s(buf, "%llu", (unsigned long long)sid);
    return buf;
}

static std::vector<PlayerInfo> ReadPlayers(uintptr_t gworld_ptr)
{
    std::vector<PlayerInfo> result;
    uintptr_t gworld = SafeReadPtr(gworld_ptr);
    if (!gworld) { DebugLog("ReadPlayers: gworld NULL"); return result; }

    // Python-confirmed direct path: UWorld+0x038 = GameState, GameState+0x090 = PlayerArray
    uintptr_t gs = SafeReadPtr(gworld + 0x038);
    if (!IsHeapUObject(gs)) {
        DebugLog("ReadPlayers: GameState invalid");
        return result;
    }

    uintptr_t pa_data = SafeReadPtr(gs + 0x090);
    int32_t   pa_cnt  = 0;
    if (!SafeReadInt32(gs + 0x098, pa_cnt) || pa_cnt <= 0 || pa_cnt > 64) {
        static int s_empty = 0;
        if (++s_empty <= 3 || s_empty % 60 == 0) {
            char buf[128];
            sprintf_s(buf, "ReadPlayers: pa_cnt=%d gs=0x%llX pa_data=0x%llX",
                      pa_cnt, (unsigned long long)gs, (unsigned long long)pa_data);
            DebugLog(buf);
        }
        return result;
    }

    for (int i = 0; i < pa_cnt; ++i) {
        uintptr_t ps = SafeReadPtr(pa_data + i * 8);
        if (!IsHeapUObject(ps)) continue;

        std::wstring name = ReadSteamName(ps);
        if (name.empty()) name = L"Player";
        std::string steam_id = ReadSteamId(ps);

        float x = 0, y = 0, z = 0;
        bool  online = false;

        // Fast path: cached offsets
        uintptr_t pawn = SafeReadPtr(ps + g_OffPawn);
        if (pawn && IsHeapUObject(pawn)) {
            uintptr_t comp = SafeReadPtr(pawn + g_OffComp);
            if (comp && IsHeapUObject(comp)) {
                float tx = SafeReadFloat(comp + g_OffLoc);
                float ty = SafeReadFloat(comp + g_OffLoc + 4);
                float tz = SafeReadFloat(comp + g_OffLoc + 8);
                if (IsPlausibleCoord(tx, ty, tz)) {
                    x = tx; y = ty; z = tz;
                    online = true;
                }
            }
        }

        // Slow path: auto-discover offsets when fast path fails
        if (!online && !g_OffVerified) {
            if (AutoDiscoverOffsets(ps)) {
                pawn = SafeReadPtr(ps + g_OffPawn);
                if (pawn && IsHeapUObject(pawn)) {
                    uintptr_t comp = SafeReadPtr(pawn + g_OffComp);
                    if (comp && IsHeapUObject(comp)) {
                        x = SafeReadFloat(comp + g_OffLoc);
                        y = SafeReadFloat(comp + g_OffLoc + 4);
                        z = SafeReadFloat(comp + g_OffLoc + 8);
                        if (IsPlausibleCoord(x, y, z)) online = true;
                    }
                }
            }
        }

        // Log first 15 player reads in detail
        static int s_plog = 0;
        if (s_plog < 15) {
            ++s_plog;
            uintptr_t dbg_pawn = SafeReadPtr(ps + g_OffPawn);
            uintptr_t dbg_comp = dbg_pawn ? SafeReadPtr(dbg_pawn + g_OffComp) : 0;
            float dbg_x = dbg_comp ? SafeReadFloat(dbg_comp + g_OffLoc)     : 0.f;
            float dbg_y = dbg_comp ? SafeReadFloat(dbg_comp + g_OffLoc + 4) : 0.f;
            float dbg_z = dbg_comp ? SafeReadFloat(dbg_comp + g_OffLoc + 8) : 0.f;
            char pbuf[256];
            sprintf_s(pbuf,
                "  ps=0x%llX pawn=0x%llX(%d) comp=0x%llX(%d) xyz=%.0f,%.0f,%.0f plaus=%d online=%d",
                (unsigned long long)ps,
                (unsigned long long)dbg_pawn, (int)IsHeapUObject(dbg_pawn),
                (unsigned long long)dbg_comp, (int)(dbg_comp && IsHeapUObject(dbg_comp)),
                dbg_x, dbg_y, dbg_z,
                (int)IsPlausibleCoord(dbg_x, dbg_y, dbg_z),
                (int)online);
            DebugLog(pbuf);
        }

        result.push_back({name, steam_id, online, x / 100.f, y / 100.f, z / 100.f});
    }

    static int s_n = 0;
    if (++s_n <= 5 || s_n % 30 == 0) {
        char buf[192];
        sprintf_s(buf, "ReadPlayers#%d gworld=0x%llX gs=0x%llX pa_cnt=%d players=%d offVerified=%d",
                  s_n, (unsigned long long)gworld, (unsigned long long)gs,
                  pa_cnt, (int)result.size(), (int)g_OffVerified);
        DebugLog(buf);
    }
    return result;
}

// ---------------------------------------------------------------------------
// JSON schreiben
// ---------------------------------------------------------------------------

static std::string WstrToUtf8(const std::wstring& w)
{
    if (w.empty()) return {};
    int sz = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), -1, nullptr, 0, nullptr, nullptr);
    std::string s(sz, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.c_str(), -1, &s[0], sz, nullptr, nullptr);
    if (!s.empty() && s.back() == '\0') s.pop_back();
    return s;
}

static void WriteJson(const std::vector<PlayerInfo>& players)
{
    std::ostringstream j;
    j << std::fixed << std::setprecision(2);

    int online = 0;
    for (auto& p : players) if (p.online) ++online;

    j << "{\n  \"timestamp\": " << static_cast<double>(time(nullptr)) << ",\n";
    j << "  \"player_count\": " << online << ",\n";
    j << "  \"players\": [\n";
    for (size_t i = 0; i < players.size(); ++i) {
        auto& p = players[i];
        j << "    {\"name\": \"" << WstrToUtf8(p.name) << "\", "
          << "\"steam_id\": \"" << p.steam_id << "\", "
          << "\"online\": " << (p.online ? "true" : "false");
        if (p.online)
            j << ", \"x_m\": " << p.x_m << ", \"y_m\": " << p.y_m << ", \"z_m\": " << p.z_m;
        j << "}";
        if (i + 1 < players.size()) j << ",";
        j << "\n";
    }
    j << "  ]\n}\n";

    std::string json_str = j.str();

    // Always write file
    std::wstring tmp = std::wstring(g_OutputPath) + L".tmp";
    { std::ofstream f(tmp); if (f) f << json_str; }
    MoveFileExW(tmp.c_str(), g_OutputPath, MOVEFILE_REPLACE_EXISTING);

    // Also POST if ServerUrl is configured
    if (g_ServerUrl[0]) PostJson(json_str);
}

// ---------------------------------------------------------------------------
// Mod-Thread
// ---------------------------------------------------------------------------

static bool g_threadStarted = false;

static DWORD WINAPI ModThread(LPVOID)
{
    DebugLog("ModThread gestartet");

    MODULEINFO mi{};
    GetModuleInformation(GetCurrentProcess(), GetModuleHandleW(nullptr), &mi, sizeof(mi));
    g_ModBase = reinterpret_cast<uintptr_t>(mi.lpBaseOfDll);
    g_ModSize = mi.SizeOfImage;

    char buf[128];
    sprintf_s(buf, "ModBase=0x%llX ModSize=0x%llX",
              (unsigned long long)g_ModBase, (unsigned long long)g_ModSize);
    DebugLog(buf);

    uintptr_t gworld_ptr = 0;
    int ticks = 0;

    for (;;) {
        if (!gworld_ptr) {
            gworld_ptr = FindGWorldPtr();
            if (gworld_ptr) {
                sprintf_s(buf, "GWorld gefunden: 0x%llX", (unsigned long long)gworld_ptr);
                DebugLog(buf);
            } else if (++ticks % 15 == 0) {
                sprintf_s(buf, "GWorld nicht gefunden (Versuch %d)", ticks);
                DebugLog(buf);
            }
        }

        if (gworld_ptr) {
            auto players = ReadPlayers(gworld_ptr);
            WriteJson(players);
            // Re-scan if no player is online for 30 consecutive ticks
            // (player left prospect, or we picked the wrong world)
            static int s_empty_ticks = 0;
            bool any_online = false;
            for (auto& p : players) if (p.online) { any_online = true; break; }
            if (!any_online) {
                if (++s_empty_ticks >= 30) {
                    DebugLog("GWorld no-online 30 ticks, re-scanning");
                    gworld_ptr    = 0;
                    s_empty_ticks = 0;
                    // keep g_OffVerified=true (hardcoded offsets stay valid)
                }
            } else {
                s_empty_ticks = 0;
            }
        }

        Sleep(g_IntervalMs);
    }
}

static void StartModThread()
{
    if (g_threadStarted) return;
    g_threadStarted = true;
    DebugLog("StartModThread()");
    CloseHandle(CreateThread(nullptr, 0, ModThread, nullptr, 0, nullptr));
}

// ---------------------------------------------------------------------------
// LiveMapMod – DaedalusLoader CoreMod Subklasse
// ---------------------------------------------------------------------------

class LiveMapMod : public Mod
{
public:
    LiveMapMod()
    {
        ModName         = "LiveMapMod";
        ModVersion      = "1.0";
        ModDescription  = "Writes live player positions to live_players.json";
        ModAuthors      = "N30Z";
        ModLoaderVersion = "2.3.0";
        ModRef = this;
        CompleteModCreation();  // registriert bei DaedalusLoader, loggt "Core Mod Created"
    }

    // DaedalusLoader ruft InitializeMod() nach dem Laden aller CoreMods
    void InitializeMod() override
    {
        DebugLog("InitializeMod() aufgerufen");
        StartModThread();
    }

    // Alle anderen Callbacks leer lassen
    void InitGameState() override         {}
    void DrawImGui() override             {}
    void BeginPlay(UE4::AActor*) override {}
    void PostBeginPlay(std::wstring, UE4::AActor*) override {}
    void DX11Present(ID3D11Device*, ID3D11DeviceContext*, ID3D11RenderTargetView*) override {}
    void DX12Present(IDXGISwapChain*, ID3D12GraphicsCommandList*) override {}
    void OnModMenuButtonPressed() override {}
};

// ---------------------------------------------------------------------------
// DLL-Einstiegspunkt
// ---------------------------------------------------------------------------

BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH) {
        g_hSelf = hModule;
        DisableThreadLibraryCalls(hModule);
        LoadConfig();
        DebugLog("DllMain: LiveMapMod geladen");
        new LiveMapMod();  // setzt ModRef = this, ruft CompleteModCreation()
        // Thread hier starten – auf dem Server wird InitializeMod() nie aufgerufen
        // (hookInitGameState schlägt fehl). g_threadStarted verhindert Doppelstart.
        StartModThread();
    }
    return TRUE;
}
