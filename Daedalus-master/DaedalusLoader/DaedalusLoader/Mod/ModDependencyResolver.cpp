#include "ModDependencyResolver.h"
#include "../Mod/Mod.h"
#include "../Utilities/Logger.h"
#include <unordered_map>
#include <unordered_set>
#include <algorithm>

namespace ModDependencyResolver
{
	bool SortByDependencies(std::vector<Mod*>& mods)
	{
		if (mods.size() <= 1)
			return true;

		// Build name -> index map
		std::unordered_map<std::string, int> nameToIndex;
		for (int i = 0; i < (int)mods.size(); i++)
		{
			if (!mods[i]) continue;
			nameToIndex[mods[i]->ModName] = i;
		}

		// Log dependency info
		for (auto* mod : mods)
		{
			if (!mod) continue;
			if (mod->Dependencies.empty())
			{
				Log::Info("Dependency: %s has no dependencies", mod->ModName.c_str());
			}
			else
			{
				for (const auto& dep : mod->Dependencies)
				{
					Log::Info("Dependency: %s requires %s", mod->ModName.c_str(), dep.c_str());
				}
			}
		}

		// Validate all dependencies exist
		for (auto* mod : mods)
		{
			if (!mod) continue;
			for (const auto& dep : mod->Dependencies)
			{
				if (nameToIndex.find(dep) == nameToIndex.end())
				{
					Log::Error("Dependency: %s requires '%s' which is not loaded!", 
						mod->ModName.c_str(), dep.c_str());
					return false;
				}
			}
		}

		// Kahn's algorithm for topological sort
		int n = (int)mods.size();
		std::vector<int> inDegree(n, 0);
		std::vector<std::vector<int>> adj(n); // adj[i] = list of mods that depend on i

		for (int i = 0; i < n; i++)
		{
			if (!mods[i]) continue;
			for (const auto& dep : mods[i]->Dependencies)
			{
				auto it = nameToIndex.find(dep);
				if (it != nameToIndex.end())
				{
					adj[it->second].push_back(i); // dep -> i (i depends on dep)
					inDegree[i]++;
				}
			}
		}

		// Start with mods that have no dependencies
		std::vector<int> queue;
		for (int i = 0; i < n; i++)
		{
			if (inDegree[i] == 0)
				queue.push_back(i);
		}

		std::vector<int> sorted;
		sorted.reserve(n);
		size_t front = 0;

		while (front < queue.size())
		{
			int curr = queue[front++];
			sorted.push_back(curr);

			for (int next : adj[curr])
			{
				inDegree[next]--;
				if (inDegree[next] == 0)
					queue.push_back(next);
			}
		}

		if ((int)sorted.size() != n)
		{
			// Cycle detected - find which mods are in the cycle
			Log::Error("Dependency: Circular dependency detected! The following mods are involved:");
			for (int i = 0; i < n; i++)
			{
				if (inDegree[i] > 0 && mods[i])
				{
					Log::Error("  - %s", mods[i]->ModName.c_str());
				}
			}
			return false;
		}

		// Reorder the mods vector
		std::vector<Mod*> reordered;
		reordered.reserve(n);
		for (int idx : sorted)
		{
			reordered.push_back(mods[idx]);
		}

		// Log final order
		Log::Info("Dependency: Load order resolved:");
		for (int i = 0; i < (int)reordered.size(); i++)
		{
			if (reordered[i])
				Log::Info("  %d. %s", i + 1, reordered[i]->ModName.c_str());
		}

		mods = reordered;
		return true;
	}
};
