# Profile packs — console-side compile destination

At bootstrap or `adminme pack install`, profile packs compile from
`packs/profiles/<id>/views/*.jsx` into the instance's installed-packs
directory (resolved via InstanceConfig — never hardcoded per
SYSTEM_INVARIANTS.md §15 / DECISIONS.md §D15). The console reads the
compiled artifacts from the resolved location at serve time.

This directory is the console-side staging area for development;
production-path compile output lives under the instance's packs directory.

Filled in by prompt 14b.
