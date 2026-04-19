#pragma once
#include <vector>
#include <string>

class Mod;

namespace ModDependencyResolver
{
	// Sorts CoreMods in-place so dependencies are initialized before dependents.
	// Returns true on success; false if a cycle or missing dependency is detected.
	// On failure, logs the specific problem and leaves the vector unchanged.
	bool SortByDependencies(std::vector<Mod*>& mods);
};
