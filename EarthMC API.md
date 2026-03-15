# EarthMC API (Aurora) Reference

Base URL: `https://api.earthmc.net/v3/aurora`

## General Notes
- Responses are JSON. Many fields are optional and omitted if not present.
- Some arrays preserve in-game order (coordinates, permissions).
- Use POST with a `query` array for detailed objects (towns, nations, players, quarters, location), and optional `template` to limit fields.
- Timestamps are Unix milliseconds (UTC).

## Endpoints

### Server
- **GET** `/` — server status and stats.

Example:
```json
{
  "version": "1.19.4",
  "timestamps": { "newDayTime": 43200, "serverTimeOfDay": 15235 },
  "stats": { "numOnlinePlayers": 184, "numResidents": 27858, ... }
}
```

### Towns
- **GET** `/towns` — list of all towns with name/uuid.
- **POST** `/towns` — detailed towns; body:
```json
{
  "query": ["uuid-or-name", "AnotherTown"],
  "template": { "name": true, "uuid": true, "mayor": true, "residents": true, "timestamps": true, "stats": true }
}
```
Key fields returned (if requested): `name`, `uuid`, `mayor`, `nation`, `timestamps` (`registered`, `joinedNationAt`, `ruinedAt`), `status`, `stats` (`numResidents`, `numTownBlocks`, `balance`), `residents` (array), `coordinates`.

### Nations
- **GET** `/nations` — list of all nations with name/uuid.
- **POST** `/nations` — detailed nations; body:
```json
{
  "query": ["uuid-or-name", "Yukon"]
}
```
Key fields: `name`, `uuid`, `king`, `capital`, `timestamps.registered`, `stats` (`numTowns`, `numResidents`), `towns` (array of name/uuid), `residents`, `allies`, `enemies`.

### Players
- **GET** `/players` — list of all residents with name/uuid.
- **POST** `/players` — detailed players; body:
```json
{
  "query": ["uuid-or-name", "Fruitloopins"],
  "template": { "name": true, "uuid": true, "timestamps": true, "status": true }
}
```
Key fields: `name`, `uuid`, `timestamps` (`registered`, `joinedTownAt`, `lastOnline`), `status` (`isOnline`, `isNPC`, `isMayor`, `isKing`), `town`, `nation`.

### Quarters
- **GET** `/quarters` — list all quarters (UUID + name or null).
- **POST** `/quarters` — detailed quarters; body:
```json
{
  "query": ["quarter-uuid-1", "quarter-uuid-2"]
}
```
Key fields: `uuid`, `type`, `owner`, `town`, `timestamps` (`registered`, `claimedAt`), `status.isEmbassy`, `stats.price`, `stats.volume`.

### Nearby
- **POST** `/nearby` — search for elements near a town or coordinate.
Body example (town target):
```json
{
  "query": [
    { "target_type": "TOWN", "target": "Melbourne", "search_type": "TOWN", "radius": 100 }
  ]
}
```
Body example (coordinate target):
```json
{
  "query": [
    { "target_type": "COORDINATE", "target": [2000, 10000], "search_type": "TOWN", "radius": 10000 }
  ]
}
```
Response: array of arrays of nearby towns (name/uuid) per query.

### Discord
- **POST** `/discord` — resolve between Discord ID and Minecraft UUID.
Body:
```json
{
  "query": [
    { "type": "minecraft", "target": "minecraft-uuid" },
    { "type": "discord", "target": "discord-id" }
  ]
}
```

### Location
- **POST** `/location` — Towny info for coordinates.
Body:
```json
{
  "query": [ [0,0], [100,100] ]
}
```
Response items: `isWilderness`, `town` (name/uuid), `nation` (name/uuid).

### Mystery Master
- **GET** `/mm` — top 50 players participating in Mystery Master (name/uuid/change).

## Templates (POST requests)
- Optional `template` object lets you request only top-level fields that are `true`.
- Nested control is not supported; setting a parent to `true` returns its full nested content.
- Fields not set to `true` (or invalid keys) are omitted.

Example:
```json
{
  "query": ["Mojo", "JavaScript"],
  "template": { "name": true, "mayor": true }
}
```
Response only includes requested top-level fields.
