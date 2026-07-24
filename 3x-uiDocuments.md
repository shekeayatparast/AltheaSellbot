# 3X-UI Panel API Reference  
**Comprehensive guide for building a sales bot**  

This document describes every endpoint and data structure you need to interact with a **3X-UI** panel programmatically.  
Use it as the foundation for a Telegram/Vendor bot that creates, manages, and invoices VPN users.

---

## Table of Contents
1. [Base URL & Authentication](#base-url--authentication)  
2. [General API Response Format](#general-api-response-format)  
3. [Authentication Endpoints](#authentication-endpoints)  
4. [Inbounds (Proxy Configurations)](#inbounds)  
5. [Clients (Users / Customers)](#clients)  
6. [Nodes (Remote Panels)](#nodes)  
7. [Hosts (Per‑Inbound Overrides)](#hosts)  
8. [Backup to Telegram](#backup)  
9. [Settings & API Token Management](#settings)  
10. [Xray Configuration & Outbounds](#xray-settings)  
11. [Subscription Server Endpoints](#subscription-server)  
12. [WebSocket (Real‑Time Updates)](#websocket)  
13. [Important Data Schemas](#important-schemas)  
14. [Workflow for a Sales Bot](#sales-bot-workflow)

---

## Base URL & Authentication

All API calls start with the panel’s address followed by `/panel/api/`:
```
https://<panel-ip>:<port>/<webBasePath>/panel/api/...
```
Replace `<webBasePath>` with the value set in **Settings → Web Base Path** (empty by default).

### Authentication Methods
1. **Session Cookie** – used by the web UI.  
   Obtain via `POST /login`, then include the cookie `3x-ui` with every request.  
   *Bots should avoid this because sessions expire.*

2. **Bearer Token (API Token)** – **recommended for bots**.  
   - Create in the panel UI at **Settings → Security → API Token**, or via the API itself if you have admin credentials.  
   - Send the header:  
     ```
     Authorization: Bearer <your-plaintext-token>
     ```
   - Tokens have full admin privileges; store them securely.  
   - The plaintext token is shown **only once** during creation (the server stores a SHA‑256 hash).  
   - CSRF checks are disabled for Bearer-authenticated requests, so no extra headers are needed.

---

## General API Response Format

Every endpoint returns a JSON object with three keys:

```json
{
  "success": true,
  "msg": "Human-readable message",
  "obj": { ... }         // the payload – may be object, array, string, or number
}
```
- On error, `success` is `false` and `msg` explains the problem.  
- The `obj` content depends on the endpoint; it is described for each path below.

---

## Authentication Endpoints

| Method | URL | Purpose |
|--------|-----|---------|
| `POST` | `/login` | Authenticate with username/password → sets session cookie |
| `POST` | `/logout` | Clears session cookie |
| `GET`  | `/csrf-token` | Returns CSRF token (only needed for cookie‑based UI sessions) |
| `POST` | `/getTwoFactorEnable` | Returns whether 2FA is enabled (used by login page) |

### `POST /login`
**Request body** (JSON):
```json
{
  "username": "admin",
  "password": "admin",
  "twoFactorCode": "123456"   // omit if 2FA is off
}
```
**Response**:
```json
{
  "success": true,
  "msg": "Logged in successfully",
  "obj": {}
}
```

---

## Inbounds

Inbounds are your VPN “listening ports” (VLESS, VMess, Trojan, etc.).  
A sales bot typically reads the list of inbounds to present options to customers, but does **not** need to modify them.  
All endpoints are under `/panel/api/inbounds/`.

| Endpoint | Method | Summary |
|----------|--------|---------|
| `/inbounds/list` | `GET` | Full list of inbounds with client traffic |
| `/inbounds/list/slim` | `GET` | Lightweight list (no UUID/passwords) – suitable for pickers |
| `/inbounds/options` | `GET` | Simplified picker projection (id, remark, protocol, port, etc.) |
| `/inbounds/get/{id}` | `GET` | Single inbound details |
| `/inbounds/add` | `POST` | Create a new inbound |
| `/inbounds/del/{id}` | `POST` | Delete an inbound |
| `/inbounds/bulkDel` | `POST` | Delete many inbounds |
| `/inbounds/update/{id}` | `POST` | Replace an inbound’s configuration |
| `/inbounds/setEnable/{id}` | `POST` | Toggle inbound enable flag (lightweight) |
| `/inbounds/{id}/resetTraffic` | `POST` | Reset inbound’s upload/download counters |
| `/inbounds/{id}/delAllClients` | `POST` | Remove all clients from one inbound (destructive) |
| `/inbounds/resetAllTraffics` | `POST` | Reset counters for every inbound |
| `/inbounds/import` | `POST` | Bulk‑import from JSON |
| `/inbounds/pushClientTraffics` | `POST` | Internal – receives aggregated client traffic from nodes |
| `/inbounds/{id}/fallbacks` | `GET` / `POST` | List or replace fallback rules for a master TLS inbound |
| `/inbounds/allLinks` | `GET` | Export every protocol URL across all inbounds |

### Example: `GET /panel/api/inbounds/options`
Returns a simple array for dropdowns:

```json
{
  "success": true,
  "obj": [
    {
      "enable": true,
      "id": 1,
      "listen": "",
      "port": 443,
      "protocol": "vless",
      "remark": "VLESS-443",
      "tag": "in-443-tcp",
      "tlsFlowCapable": true,
      "ssMethod": "",
      ...
    }
  ]
}
```
*Field `id` is what you use when attaching a client to an inbound.*

---

## Clients

This is **the most important resource** for a sales bot.  
A **client** is a user account that can be attached to one or more inbounds.  
Clients have email (unique ID), traffic limit, expiry date, enable flag, etc.

All client endpoints live under `/panel/api/clients/`.

### 1. List / Search Clients

#### `GET /panel/api/clients/list`
Returns **every** client with full details (UUID, password, flow…).  
**Not recommended for large panels** – use paged version instead.

#### `GET /panel/api/clients/list/paged`
Filter, sort, and paginate clients server‑side.  
**Query parameters** (all required for filtering):

| Param | Type | Description |
|-------|------|-------------|
| `page` | integer | 1‑based page number, default 1 |
| `pageSize` | integer | rows per page (max 200) |
| `search` | string | case‑insensitive match on email / subId / comment |
| `filter` | string | status bucket: `online`, `active`, `deactive`, `depleted`, `expiring` |
| `protocol` | string | protocol name (vless, vmess, trojan, …) – filters clients attached to such inbounds |
| `sort` | string | sort key: `enable`, `email`, `inboundIds`, `traffic`, `remaining`, `expiryTime` |
| `order` | string | `ascend` or `descend` |

**Response**:
```json
{
  "success": true,
  "obj": {
    "items": [ ... slim client objects ... ],
    "total": 2000,
    "filtered": 47,
    "page": 1,
    "pageSize": 25,
    "summary": {
      "total": 2000,
      "active": 1850,
      "online": ["alice@example.com"],
      "depleted": [],
      "expiring": [],
      "deactive": []
    }
  }
}
```
*The summary is computed across the full database and stays stable during pagination.*  
Each item in `items` contains: `email`, `subId`, `enable`, `totalGB`, `expiryTime`, `limitIp`, `reset`, `inboundIds`, `traffic` (up/down/enable), `createdAt`, `updatedAt`.  
**Note:** UUID/password/flow/security are **not** included – fetch `/get/{email}` to get those.

### 2. Single Client Operations

#### `GET /panel/api/clients/get/{email}`
Returns **complete** client data including UUID, password, auth secrets, and the list of attached `inboundIds`.

#### `POST /panel/api/clients/add`
Create a new client and attach it to one or more inbounds.  
**Request body**:
```json
{
  "client": {
    "email": "alice@example.com",
    "totalGB": 50,           // traffic limit in GB
    "expiryTime": 1735689600000,   // unix timestamp in milliseconds (0 = never)
    "tgId": 123456789,       // Telegram user ID (optional)
    "limitIp": 2,            // max simultaneous IPs (0 = unlimited)
    "enable": true
    // other protocol‑specific fields (id/uuid/password) are generated automatically if omitted
  },
  "inboundIds": [3, 5]      // at least one inbound must be specified
}
```
**Response**: standard success message.

**Important for bot:**
- `totalGB` is in **gigabytes**, not bytes.  
- `expiryTime` is **Unix milliseconds**.  
- The panel auto‑generates a UUID (`id`) for VLESS/VMess, a password for Trojan/Shadowsocks, etc., so you only need to supply universal fields.

#### `POST /panel/api/clients/update/{email}`
Replace a client’s record. The body is the **full** client JSON (same shape as in `add`).  
Changes propagate to all inbounds the client is attached to.

#### `POST /panel/api/clients/del/{email}`
Delete a client. Query param `keepTraffic=1` retains the traffic row (if you later want to resurrect the user).

### 3. Attach / Detach

#### `POST /panel/api/clients/{email}/attach`
Add an existing client to more inbounds.  
Body: `{ "inboundIds": [7, 9] }`

#### `POST /panel/api/clients/{email}/detach`
Remove a client from specific inbounds without deleting the client record.  
Body: `{ "inboundIds": [5] }`

### 4. External Links (per‑client share links / remote subs)
#### `POST /panel/api/clients/{email}/externalLinks`
Replace the client’s external links (shown in their subscription).  
Body:
```json
{
  "externalLinks": [
    { "kind": "link", "value": "vless://...", "remark": "DE" },
    { "kind": "subscription", "value": "https://provider.example/sub/abc", "remark": "Provider" }
  ]
}
```

### 5. Traffic & Quota Management

#### `GET /panel/api/clients/traffic/{email}`
Returns the client’s current traffic counters (`up`, `down`, `total` – all in **bytes**) and metadata (`expiryTime`, `enable`, etc.).  

#### `POST /panel/api/clients/resetTraffic/{email}`
Zero out the client’s up/down counters and re‑enable them if they were auto‑disabled.  

#### `POST /panel/api/clients/updateTraffic/{email}`
Manually set upload/download values.  
Body: `{ "upload": 1073741824, "download": 5368709120 }` (both in bytes).  

#### Bulk Operations

| Endpoint | Method | Body | Purpose |
|----------|--------|------|---------|
| `/clients/bulkAdjust` | `POST` | `{ "emails": ["a","b"], "addDays": 30, "addBytes": 53687091200, "flow": "xtls-rprx-vision" }` | Add time and/or traffic to many users (negative values allowed). Automatically re‑enables depleted users. |
| `/clients/bulkEnable` | `POST` | `{ "emails": ["a","b"] }` | Enable many clients |
| `/clients/bulkDisable` | `POST` | `{ "emails": ["a","b"] }` | Disable many clients |
| `/clients/bulkDel` | `POST` | `{ "emails": ["a","b"], "keepTraffic": false }` | Delete many clients |
| `/clients/bulkCreate` | `POST` | Array of `{client, inboundIds}` objects | Create many clients in one call |
| `/clients/bulkAttach` / `/bulkDetach` | `POST` | `{ "emails": [...], "inboundIds": [...] }` | Attach/detach many clients to/from many inbounds |
| `/clients/bulkResetTraffic` | `POST` | `{ "emails": [...] }` | Reset traffic for many clients |

### 6. Groups
Groups are labels that can be used to organise clients (e.g., “VIP”, “free-tier”).  
A client can belong to exactly one group at a time.

| Endpoint | Method | Summary |
|----------|--------|---------|
| `/clients/groups` | `GET` | List all groups with member counts |
| `/clients/groups/{name}/emails` | `GET` | Return emails of members |
| `/clients/groups/create` | `POST` | Create an empty group |
| `/clients/groups/rename` | `POST` | Rename a group (updates all members) |
| `/clients/groups/delete` | `POST` | Delete group, clear label from members |
| `/clients/groups/bulkAdd` | `POST` | Add many clients to a group |
| `/clients/groups/bulkRemove` | `POST` | Clear group label from many clients |
| `/clients/groups/resetTraffic` | `POST` | Reset group‑level traffic counter |

### 7. Online Status & IP Tracking

| Endpoint | Method | Summary |
|----------|--------|---------|
| `/clients/onlines` | `POST` | List emails of currently connected clients |
| `/clients/onlinesByGuid` | `POST` | Online emails grouped by node GUID |
| `/clients/activeInbounds` | `POST` | Inbound tags that had traffic per node |
| `/clients/lastOnline` | `POST` | Map of email → last seen timestamp (seconds) |
| `/clients/ips/{email}` | `POST` | List source IPs that used this client’s credentials |
| `/clients/clearIps/{email}` | `POST` | Clear recorded IPs |

### 8. Subscription & Protocol Links

#### `GET /panel/api/clients/links/{email}`
Returns an array of protocol URLs (`vless://...`, `vmess://...`, `trojan://...`, `ss://...`, `hysteria://...`) for **every inbound the client is attached to**.  
Use this to give the customer their connection link.

#### `GET /panel/api/clients/subLinks/{subId}`
Same as above but for a subscription ID (multiple clients can share one `subId`).  
Returns the links as a JSON array.

#### `GET /panel/api/clients/subLinks/{subId}` and the subscription server (port 10882 by default) together form the actual subscription service.

---

## Nodes
If your panel is a **central controller** for other 3x‑UI instances, the `/nodes/` endpoints let you manage them.  
A sales bot might not need these unless you operate a multi‑node setup.

Key endpoints: `list`, `get/{id}`, `add`, `update/{id}`, `del/{id}`, `setEnable/{id}`, `test`, `probe/{id}`, `certFingerprint`, `inbounds`, `updatePanel`, `history/...`

When a client is created on a specific inbound that is hosted on a node, the node handle traffic automatically – the central panel syncs client data and collects traffic stats.

---

## Hosts
Host groups provide per‑inbound overrides (e.g., different CDN endpoints) that appear in subscription files.  
Not directly needed by a basic sales bot, but you can use them to offer varied exit points.

---

## Backup
`POST /panel/api/backuptotgbot` – sends a database backup to the configured Telegram chat(s).  
The bot can trigger a manual backup before major changes.

---

## Settings

### Panel Configuration
`POST /panel/api/setting/all` – returns every panel setting (web, Telegram, subscription, LDAP, SMTP, …).  
`POST /panel/api/setting/update` – persist the full settings blob.

### Admin Credentials
`POST /panel/api/setting/updateUser` – change admin username/password.  
Body: `{ "oldUsername", "oldPassword", "newUsername", "newPassword" }`.

### API Token Management
Tokens are how your bot authenticates.

| Endpoint | Method | Summary |
|----------|--------|---------|
| `/setting/apiTokens` | `GET` | List all tokens (metadata only, no plaintext) |
| `/setting/apiTokens/create` | `POST` | Create a new token. **Body**: `{ "name": "my-bot" }`. Returns the plaintext token in `obj.token` – **copy it immediately**! |
| `/setting/apiTokens/delete/{id}` | `POST` | Permanently delete a token |
| `/setting/apiTokens/setEnabled/{id}` | `POST` | Toggle enabled/disabled |

**Bot setup:**  
1. Use the panel UI to create a token, or  
2. Log in with admin credentials, call `/setting/apiTokens/create`, and store the returned `token` string.

### Other
- `POST /setting/restartPanel` – restart the panel process (bot can do this for maintenance).  
- `POST /setting/testSmtp` / `testTgBot` – verify notification settings.

---

## Xray Settings
These endpoints manage the Xray core configuration, outbounds, Warp, NordVPN, etc.  
A sales bot usually does **not** need to change these, but if you want to test network connectivity or adjust routing, they are available under `/panel/api/xray/`.

---

## Subscription Server

The panel runs a separate HTTP/HTTPS server for client subscriptions on the port defined in settings (default `10882`).  
**Endpoints (configurable paths):**

| Path | Purpose |
|------|---------|
| `GET /{subPath}/{subid}` | Base64‑encoded subscription (standard format) |
| `GET /{jsonPath}/{subid}` | JSON array of proxy configs |
| `GET /{clashPath}/{subid}` | Clash / Mihomo YAML config |

Default paths: `/sub/{subid}`, `/json/{subid}`, `/clash/{subid}`.  
These endpoints are **not** under `/panel/api/`; they are served by the subscription server directly.  
When you enable subscriptions in settings, you can give customers the URL: `https://panel:10882/sub/abcd1234` and the panel will output the user’s proxy links automatically.

---

## WebSocket
Real‑time status updates at `ws://<panel>/ws` (requires session cookie).  
Bots can ignore this or use it to monitor server health.

---

## Important Schemas

### Msg (General Response)
```json
{
  "success": true,
  "msg": "string",
  "obj": {}
}
```

### Client (user account)
| Field | Type | Description |
|-------|------|-------------|
| `email` | string | Unique identifier (used as login name for the bot) |
| `enable` | boolean | Whether the client is active |
| `totalGB` | int64 | Traffic limit in **gigabytes** (0 = unlimited) |
| `expiryTime` | int64 | Expiration timestamp in **milliseconds** (0 = never) |
| `limitIp` | int | Max simultaneous IPs (0 = unlimited) |
| `tgId` | int64 | Telegram user ID for panel notifications |
| `subId` | string | Subscription ID (used in subscription URLs) |
| `reset` | int | Traffic reset period in days (0 = never) |
| `flow` | string | XTLS flow (e.g., `"xtls-rprx-vision"`) |
| `id` / `uuid` | string | UUID for VLESS/VMess (auto‑generated) |
| `password` | string | Password for Trojan/Shadowsocks (auto‑generated) |
| `security` | string | Encryption method (e.g., `"auto"`, `"aes-128-gcm"`) |
| `comment` | string | Free‑form comment |

Fields `id`, `password`, `auth`, `flow`, `security`, `secret`, `preSharedKey`, `privateKey`, `publicKey`, `reverse` are protocol‑specific and are filled by the server when omitted.

### ClientRecord (as returned by list endpoints)
Same as Client but with additional DB fields: `id` (row ID), `createdAt`, `updatedAt`, etc.

### ClientTraffic
| Field | Type | Description |
|-------|------|-------------|
| `email` | string | Client email |
| `up` | int64 | Upload bytes |
| `down` | int64 | Download bytes |
| `total` | int64 | Traffic limit in **bytes** (note: different unit than `totalGB`) |
| `expiryTime` | int64 | Expiry timestamp (ms) |
| `enable` | bool | Enabled flag |
| `inboundId` | int | ID of the inbound this stat row belongs to |
| `lastOnline` | int64 | Last seen timestamp (ms) |
| `subId` | string | Subscription ID |

### InboundOption (for pickers)
Basic inbound info: `id`, `remark`, `tag`, `protocol`, `port`, `enable`, `tlsFlowCapable`, `ssMethod`, `wgPublicKey`, etc.

### Inbound (full)
Contains `settings`, `streamSettings`, `sniffing` (nested objects), `clientStats` array, `total` (traffic limit in bytes), `expiryTime`, `port`, `protocol`, `remark`, etc.

### Others
`Host`, `HostGroup`, `Node`, `RealityScanResult`, etc. – see the original spec if you need them.

---

## Sales Bot Workflow

Here is a typical flow for a bot that sells VPN accounts:

### 1. Authentication
- Obtain an API token (via UI or one‑time login).  
- Store it securely and send it in every request:  
  ```
  Authorization: Bearer <token>
  ```

### 2. List Available Inbounds (offer to customers)
`GET /panel/api/inbounds/options` → show customer protocol/port choices.

### 3. Create a New Client
`POST /panel/api/clients/add`  
Provide:
- `email` (generated or given by customer)  
- `totalGB` (e.g., `10` for 10 GB)  
- `expiryTime` (timestamp in ms, e.g., `Date.now() + 30*24*3600*1000` for 30 days)  
- `enable: true`  
- `inboundIds` from the customer’s choice  
- optionally `limitIp` and `subId`

### 4. Deliver Connection Details
`GET /panel/api/clients/links/{email}` → returns array of `vless://...` URLs.  
Send the first URL to the user.

Alternatively, enable subscriptions in panel settings and give the user their subscription URL: `https://panel:10882/sub/{subId}`.

### 5. Monitor Usage & Expiry
- `GET /panel/api/clients/traffic/{email}` → check remaining traffic.  
- Compare `(totalGB * 1073741824) - (up + down)` to warn near depletion.  
- Compare `expiryTime` with current time.

### 6. Renew / Extend Service
- To add time and traffic: `POST /panel/api/clients/bulkAdjust` with `addDays` and `addBytes`.  
  *Example*: add 30 days and 10 GB → `addBytes: 10737418240`  
- Or update the client directly: `POST /panel/api/clients/update/{email}` with new `totalGB` and `expiryTime`.

### 7. Disable / Enable Accounts
- `POST /panel/api/clients/{email}/update` with `enable: false`, or  
- `POST /panel/api/clients/bulkDisable` / `bulkEnable`.

### 8. Delete Expired / Depleted Users (cleanup)
- `POST /panel/api/clients/delDepleted` (deletes all that are exhausted)  
- `POST /panel/api/clients/bulkDel` with a list of specific emails.

### 9. Send Backups
- `POST /panel/api/backuptotgbot` – uses the panel’s own Telegram bot to send a DB backup.

