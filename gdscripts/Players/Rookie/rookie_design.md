# Greedy-Heuristics

## Initial Calculation (Reinforce)

Compute weighted attack opportunities:
- Find all legal attacker → defender pairs
- Weight = (attacking_units / defending_units) * MISSION_FACTOR
- MISSION_FACTOR = 10 if:
  - Defender territory is part of mission continent, OR
  - Defender territory is owned by mission player target, OR
  - Special mission conditions (see below)
- MISSION_FACTOR = 1 otherwise

- filter several best options
- Normalize weights to probabilities
- Randomly select an attack based on these weights

### Special Mission Cases

20 Territories Mission:
- MISSION_FACTOR = 1 (no preference for any specific territories)

3 Continents Mission:
- MISSION_FACTOR = 10 for three continents where least lands are missing
- MISSION_FACTOR = 1 for all other continents

1 Continent of Choice Mission:
- MISSION_FACTOR = 10 for the continent where least lands are missing, but it is not Eucalypta or Peaks
- MISSION_FACTOR = 1 for all other continents

## REINFORCE

Decision gate:
- If any owned territory has > 3 units: pass
- Else: consolidate up to 4 units toward high-weight attackers:
  - Prefer adjacent allied sources with > 1 unit
  - Strengthen attackers with strong weights that have < 3 units

## ATTACK

- Execute the pre-selected weighted attack from REINFORCE
- One-round combat for now

## DEPLOY

- Deploy randomly (temporary)
- Note: Replace with mission-weighted deployment later

## FORTIFY

- Recalculate owned territories (post-attack changes)
- For each adjacent owned pair:
  - If unit difference > 1, move 1 unit from higher to lower
- Then pass