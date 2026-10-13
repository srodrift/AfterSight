# AFTERSIGHT
**Your eyes see it. Your AI remembers it.**

A personal memory brain for smart glasses. AFTERSIGHT turns what your glasses see into timestamped memories. It reasons over them in time order to work out the *latest known state* of things, answers with evidence, and takes actions after you approve them.

Built for the Company Brain Hackathon (Oct 12, 2026). There's no real Ray-Ban hardware. The camera is simulated (see Limitations).

## Remember → Reason → Act
- **Remember:** each glasses capture becomes a memory (time, place, caption, detected objects). It's written to **HydraDB** as a `type="memory"` record and mirrored to a local event log.
- **Reason:** a **deterministic temporal state engine** replays every memory in time order:
  - *observed* counts are ground truth
  - *used* and *added* events produce *estimates*
  - a *purchase* never counts as being at home until the item is seen there
  - when a later observation corrects an estimate, that correction is recorded

  The LLM doesn't do the arithmetic. It interprets the question, uses the retrieved HydraDB evidence plus the engine state, and answers with *when* and *where* it last saw something. It never claims certainty it doesn't have (for example, "I saw the medication bottle but can't confirm you took it").
- **Act:** when something is low or you ask what to buy, the agent *proposes* shopping-list items. Nothing is added until you approve.

## Run
```bash
pip install -r requirements.txt
cp .env.example .env      # add HYDRA_DB_API_KEY and an Anthropic or OpenAI key
streamlit run app.py
```

## Live Vision (wearable camera mode)
**VISION → ◉ Live Vision → START VISION.** The rear camera opens inside AFTERSIGHT. Tap **REMEMBER THIS** and the current frame goes through:
1. **Vision:** the frame is sent to the vision model.
2. **Understanding:** objects, counts and the kind of place are extracted.
3. **Memory:** the memory is saved locally with its photo, then written to HydraDB. The status pill reports honestly whether the HydraDB write succeeded.

The camera then goes back to live. Captures show up in MEMORY, RECALL and Rewind. Demo mode and Upload are still there as fallbacks.

### On your iPhone (needs HTTPS)
Safari only allows the camera on secure pages, so use a Cloudflare quick tunnel. It needs no account.
```bash
brew install cloudflared
# 1. set a password first so the public link is protected:
echo "APP_PASSWORD=pick-a-strong-one" >> .env
# 2. run the app (terminal 1) and the tunnel (terminal 2):
streamlit run app.py
cloudflared tunnel --url http://localhost:8501
```
Open the `https://….trycloudflare.com` URL it prints on your iPhone, enter the password, then tap START VISION and allow the camera. Your API keys stay on your Mac. The page only sends frames to your Mac. Stop the tunnel (Ctrl+C) when you're done.

## Self-hosted HydraDB (open-source graph-node)
AFTERSIGHT can also store memories in a self-hosted [hydra-db/hydradb](https://github.com/hydra-db/hydradb) node through its OpenCypher HTTP API (`POST /v1/graphs/{graph}/query`). It runs alongside the managed HydraDB SDK, either one alone, or neither, in which case only the local log is used.

**Graph model:** each object in a capture becomes one timestamped node:
```
(:Observation {obs_id, memory_id, ts, epoch, object, quantity, has_quantity, place, event, source, caption})
(:Observation)-[:NEXT_SEEN]->(:Observation)    // same object, next sighting
```
- `event` is one of observed / used / added / purchased.
- `source` is camera / simulated / agent.
- `quantity` is -1 when unknown.

**Writes:** a batched `UNWIND $rows … CREATE`. They're idempotent per `obs_id`.
**Recall:** `MATCH (o:Observation) WHERE o.object IN $objects … ORDER BY o.epoch DESC`. Objects are matched from the question, so "egg" finds "eggs".

**Setup** (in `.env`):
```
HYDRA_GRAPH_URL=http://127.0.0.1:8443
HYDRA_GRAPH_TOKEN=<contents of your auth-token file>   # or HYDRA_GRAPH_TOKEN_FILE=/path/to/auth-token
```
**Verify** with a real round trip before demoing: `python graph_store.py --selftest`. It writes a node, re-writes it (expect 0 new) and reads it back. Then click **SYSTEM → Sync unsynced → HydraDB** to load existing memories into the graph.

## Demo script
1. **Vision tab:** live HUD view. Use **Rewind my day** to scrub through what the glasses saw.
2. **Recall tab:** ask "Do I have eggs?" → 2 eggs, seen at 6:05 PM. It also offers to add eggs to the list.
3. **Vision tab:** choose **Demo scenes**, then click **Use the last 2 eggs**.
4. **Recall tab:** ask "Do I have eggs?" again. The answer changes to an estimated 0, with evidence. Approve **eggs** for the shopping list.
5. Ask "Where are my keys?", "Did I take my medication?", "Where did I park?"
6. **Memory tab:** the egg timeline (6 → 2 → none left) shows the temporal reasoning.
7. **System tab:** HydraDB status, memories written, retrieval hits, and the state-engine tests (6/6) plus agent eval.

## Architecture
```
Live camera frame (getUserMedia component) / upload / demo scene → vision model
        → memory event → HydraDB (type=memory) + local event log
Question → HydraDB hybrid retrieval → temporal state engine → LLM agent
        → answer with evidence + proposed action → user approval → shopping list
```

## Limitations (honest)
- **Camera input is simulated.** The scenes are drawn frames. A real photo can be run through a vision model in the Glasses tab, but the vision model's counts aren't reliable enough to drive the main demo.
- **Retrieval fallback.** If HydraDB is unavailable or a new memory hasn't been indexed yet, the agent falls back to the local log. The evidence panel always shows which source was used.
- **Single user, no auth.** Memories are created only by deliberate captures, not continuous recording.
