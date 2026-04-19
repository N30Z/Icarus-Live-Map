#pragma once
#include <windows.h>
#include <vector>
#include <string>
#include <cstdio>
#include <io.h>

#include "../UMLDefs.h"

#define APP_NAME "Daedalus"

class LOADER_API Log
{
private:
	enum MsgType
	{
		INFO_MSG = 0,
		WARNING_MSG = 1,
		ERROR_MSG = 2,
		INFO_PRINTCONSOLE = 3
	};

	static bool IsConsoleValid()
	{
		// Check once and cache — avoids repeated CRT calls on invalid handles
		static int cached = -1;
		if (cached == -1)
		{
			HANDLE h = GetStdHandle(STD_OUTPUT_HANDLE);
			cached = (h != NULL && h != INVALID_HANDLE_VALUE) ? 1 : 0;
		}
		return cached == 1;
	}

	template <typename ...Args>
	static void LogMsg(MsgType type, const std::string& format, Args&& ...args)
	{
		auto size = std::snprintf(nullptr, 0, format.c_str(), std::forward<Args>(args)...);
		if (size < 0) size = 0;
		std::string output(size + 1, '\0');
		std::snprintf(&output[0], size + 1, format.c_str(), std::forward<Args>(args)...);
		output.resize(size); // trim null

		// Only write to console if we have a valid handle
		if (IsConsoleValid())
		{
			HANDLE hConsole = GetStdHandle(STD_OUTPUT_HANDLE);
			const char* label = "INFO";
			WORD color = 10;
			switch (type)
			{
			case WARNING_MSG: label = "WARNING"; color = 14; break;
			case ERROR_MSG: label = "ERROR"; color = 12; break;
			case INFO_PRINTCONSOLE: label = "PRINT"; color = 13; break;
			}

			// Use WriteConsoleA instead of fprintf to avoid CRT issues
			DWORD written;
			SetConsoleTextAttribute(hConsole, 11);
			WriteConsoleA(hConsole, "[", 1, &written, NULL);
			WriteConsoleA(hConsole, APP_NAME, (DWORD)strlen(APP_NAME), &written, NULL);
			SetConsoleTextAttribute(hConsole, 7);
			WriteConsoleA(hConsole, "][", 2, &written, NULL);
			SetConsoleTextAttribute(hConsole, color);
			WriteConsoleA(hConsole, label, (DWORD)strlen(label), &written, NULL);
			SetConsoleTextAttribute(hConsole, 7);
			std::string line = "] " + output + "\n";
			WriteConsoleA(hConsole, line.c_str(), (DWORD)line.size(), &written, NULL);
		}

		LogArray.push_back(output);
		Log::DumpLog();
	}

public:
	template <typename ...Args>
	static void Info(const std::string& format, Args&& ...args)
	{
		LogMsg(INFO_MSG, format, std::forward<Args>(args)...);
	}

	template <typename ...Args>
	static void Warn(const std::string& format, Args&& ...args)
	{
		LogMsg(WARNING_MSG, format, std::forward<Args>(args)...);
	}

	template <typename ...Args>
	static void Error(const std::string& format, Args&& ...args)
	{
		LogMsg(ERROR_MSG, format, std::forward<Args>(args)...);
	}

	template <typename ...Args>
	static void Print(const std::string& format, Args&& ...args)
	{
		LogMsg(INFO_PRINTCONSOLE, format, std::forward<Args>(args)...);
	}

	static void SetupErrorMessage(std::string Message) 
	{
		MessageBoxA(NULL, (Message + "\nPress OK to exit.").c_str(), "Error", MB_ICONERROR);
		abort();
	}

	static void SetupMessage(std::string Info, std::string Message)
	{
		MessageBoxA(NULL, (Message).c_str(), Info.c_str(), MB_OK | MB_SYSTEMMODAL);
	}

	static bool DumpLog()
	{
		FILE* Log = NULL;
		fopen_s(&Log, "Daedalus-Log.txt", "w+");
		if (!Log)
			return false;
		for (size_t i = 0; i < LogArray.size(); i++)
		{
			auto currentstring = LogArray[i];
			fprintf(Log, "%s\n", currentstring.c_str());
		}
		fclose(Log);
		return true;
	}

	// Public access for ImGui console window
	static const std::vector<std::string>& GetLogArray() { return LogArray; }

private:
	static std::vector<std::string> LogArray;
};