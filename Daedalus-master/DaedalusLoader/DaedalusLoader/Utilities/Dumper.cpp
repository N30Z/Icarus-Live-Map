#include "Dumper.h"
#include <windows.h>
#include "../UE4/Ue4.hpp"
#include "Globals.h"

Dumper* Dumper::DumpRef;

Dumper* Dumper::GetDumper()
{
	if (!DumpRef)
	{
		DumpRef = new Dumper();
	}
	return DumpRef;
}


bool Dumper::DumpObjectArray()
{
	if (UE4::UObject::GObjects == nullptr)
	{
		Log::Warn("DumpObjectArray: GObjects not initialized yet");
		return false;
	}

	FILE* Log = NULL;
	fopen_s(&Log, "ObjectDump.txt", "w+");
	if (!Log)
	{
		::Log::Error("DumpObjectArray: Failed to open ObjectDump.txt for writing");
		return false;
	}

	if (GameProfile::SelectedGameProfile.IsUsingFChunkedFixedUObjectArray)
	{
		for (int i = 0; i < UE4::UObject::GObjects->GetAsChunckArray().Num(); i++)
		{
			auto obj = UE4::UObject::GObjects->GetAsChunckArray().GetByIndex(i).Object;
			if (obj != nullptr)
				fprintf(Log, "[%06i] %-100s 0x%p\n", obj->GetIndex(), obj->GetFullName().c_str(), obj);
		}
	}
	else
	{
		for (int i = 0; i < UE4::UObject::GObjects->GetAsTUArray().Num(); i++)
		{
			auto obj = UE4::UObject::GObjects->GetAsTUArray().GetByIndex(i).Object;
			if (obj != nullptr)
				fprintf(Log, "[%06i] %-100s 0x%p\n", obj->GetIndex(), obj->GetFullName().c_str(), obj);
		}
	}

	fclose(Log);
	::Log::Info("Object Dump Complete!");
	::Log::SetupMessage("Done!", "Object Dump Complete!");
	return true;
}


bool Dumper::DumpEngineInfo()
{
	FILE* Log = NULL;
	fopen_s(&Log, "EngineInfo.txt", "w+");
	if (!Log)
	{
		::Log::Error("DumpEngineInfo: Failed to open EngineInfo.txt for writing");
		return false;
	}
	fprintf(Log, "#Engine Info Dump\n");
	fprintf(Log, "[GInfo]\nIsGInfoPatterns=0\nGName=0x%p\nGObject=0x%p\nGWorld=0x%p\n", GameProfile::SelectedGameProfile.GName - (DWORD64)GetModuleHandleW(0), GameProfile::SelectedGameProfile.GObject - (DWORD64)GetModuleHandleW(0), GameProfile::SelectedGameProfile.GWorld - (DWORD64)GetModuleHandleW(0));
	fprintf(Log, "\n[UObjectDef]\nIndex=0x%p\nClass=0x%p\nName=0x%p\nOuter=0x%p\n", GameProfile::SelectedGameProfile.defs.UObject.Index, GameProfile::SelectedGameProfile.defs.UObject.Class, GameProfile::SelectedGameProfile.defs.UObject.Name, GameProfile::SelectedGameProfile.defs.UObject.Outer);
	fprintf(Log, "\n[UFieldDef]\nNext=0x%p\n", GameProfile::SelectedGameProfile.defs.UField.Next);
	fprintf(Log, "\n[UStructDef]\nSuperStruct=0x%p\nChildren=0x%p\nPropertiesSize=0x%p\n", GameProfile::SelectedGameProfile.defs.UStruct.SuperStruct, GameProfile::SelectedGameProfile.defs.UStruct.Children, GameProfile::SelectedGameProfile.defs.UStruct.PropertiesSize);
	fprintf(Log, "\n[UFunctionDef]\nFunctionFlags=0x%p\nFunc=0x%p\n", GameProfile::SelectedGameProfile.defs.UFunction.FunctionFlags, GameProfile::SelectedGameProfile.defs.UFunction.Func);
	fclose(Log);
	::Log::Info("Engine Info Dump Complete!");
	::Log::SetupMessage("Done!", "Engine Info Dump Complete!");
	return true;
}

bool Dumper::DumpWorldActors()
{
	if (UE4::UObject::GObjects == nullptr)
	{
		Log::Warn("DumpWorldActors: GObjects not initialized yet");
		return false;
	}

	auto actorClass = UE4::AActor::StaticClass();
	if (!actorClass)
	{
		Log::Warn("DumpWorldActors: AActor::StaticClass() returned null");
		return false;
	}

	// Get actors using safe GObjects iteration (no ProcessEvent)
	auto actors = UE4::UObject::GetAllObjectsOfType<UE4::AActor>(actorClass, true);

	FILE* LogFile = NULL;
	fopen_s(&LogFile, "WorldActors_Dump.txt", "w+");
	if (!LogFile)
	{
		Log::Error("DumpWorldActors: Failed to open file for writing");
		return false;
	}

	fprintf(LogFile, "# World Actors Dump (%zu actors found)\n\n", actors.size());

	for (size_t i = 0; i < actors.size(); i++)
	{
		auto actor = actors[i];
		if (!actor) continue;

		// Only use GetName/GetFullName (memory reads, no ProcessEvent = thread-safe)
		std::string actorName = actor->GetName();
		auto cls = actor->GetClass();
		std::string className = cls ? cls->GetName() : "Unknown";
		std::string fullName = actor->GetFullName();

		fprintf(LogFile, "[%zu] %s (%s)\n", i, actorName.c_str(), className.c_str());
		fprintf(LogFile, "    FullName: %s\n\n", fullName.c_str());
	}

	fclose(LogFile);
	Log::Info("World Actors Dump Complete! (%zu actors)", actors.size());
	Log::SetupMessage("Done!", "World Actors Dump Complete!");
	return true;
}