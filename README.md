# AFTERSIGHT 2.0
**Your eyes see it. Your AI remembers it.**

AFTERSIGHT is a visual episodic memory for the physical world. You capture a moment with your camera, as if you were wearing smart glasses. A vision model turns the frame into structured memory: objects, colors, positions, what you were doing, readable text and the scene. That memory is stored as timestamped data in HydraDB. Later you can ask anything about what you've seen, and every answer comes with the memories that support it.

> Branch note: `main` is the build submitted to the Company Brain Hackathon (Oct 12, 2026), tagged `v8`. This branch, `aftersight-2.0`, is the post-hackathon product.

## What it does
| | |
|---|---|
| **See** | Live in-app camera, rear camera by default, using `getUserMedia`. **Remember this** captures one frame and runs it through three real stages: **Perception → Understanding → Memory**. The Memory stage only shows success after HydraDB confirms the write. Otherwise it reads *local only*. |
| **Remember** | Each capture is stored as an episodic memory. It holds a caption, the scene, objects (name, count, attributes, position), interactions such as "holding a yellow mug in the right hand", and details such as readable text. The photo is kept locally. A deterministic **temporal state engine** replays observations in order to separate *observed* facts from *inferred* changes. |
| **Ask** | Ask in free text or push-to-talk voice. Retrieval combines a local index (captions, objects, colors, places, time of day, recency) with HydraDB (OpenCypher graph and/or managed). If the answer depends on a visual detail that was never written down, the agent **re-examines the retained photo** with the vision model rather than guessing. Every answer is labeled OBSERVED, INFERRED or UNKNOWN and shows its evidence. |
| **Act** | Recommendations, such as "add eggs to the shopping list", are created as *pending* actions. Nothing happens until you **Approve** or **Dismiss**. Activity shows pending, approved, dismissed, the list and full history. |
| **Auto Memory** | Opt-in, with an explicit consent checkbox. It samples one frame every ~10 s while the camera is running, and stores a memory **only when something meaningfully changed**: a new object, a count change, or an object moved. Unchanged frames are discarded and no video is recorded. A visible HUD indicator and **Stop** button are always on screen, and a per-session cap applies. |
| **Rewind my day** | A timeline scrubber through every memory, with the captured photo or the simulated scene. It sits alongside search, time/place/source filters, per-object change chains, and a side-by-side comparison of two moments. |

## Run locally
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add OPENAI_API_KEY (or ANTHROPIC_API_KEY); voice needs OpenAI
streamlit run app.py        # http://localhost:8501 — camera & mic work on localhost
```
**Self-hosted HydraDB:** set `HYDRA_GRAPH_URL=http://127.0.0.1:8443` and `HYDRA_GRAPH_TOKEN`, then run `python graph_store.py --selftest`, which should print **PASS**. Then go to Settings and click **Sync unsynced → HydraDB**.

## Deploy (Streamlit Community Cloud)
Vercel can't host this app. It's serverless, and Streamlit needs a long-running Python server with a WebSocket. Use Streamlit Community Cloud instead:
1. On share.streamlit.io, choose New app → repo `srodrift/AfterSight` → branch `aftersight-2.0` → file `app.py`.
2. Under Advanced settings → Secrets, paste your keys in TOML form: `OPENAI_API_KEY = "…"` and **`APP_PASSWORD = "…"`**. The password gate protects your API credits.
3. The URL is HTTPS, so camera and microphone work on phones too.

A self-hosted HydraDB on your laptop (127.0.0.1) isn't reachable from the cloud. The hosted app uses managed HydraDB if `HYDRA_DB_API_KEY` is set, or the local memory log otherwise, and Settings reports which one is active. Hosted storage is ephemeral, so memories reset when the app restarts.

## Architecture
```
camera/ (getUserMedia component) ──frame──▶ analyze_frame (vision model → structured memory JSON)
   │  Auto Memory: memory_search.meaningful_change → store or discard
   ▼
add_event ──▶ local log (events.json + images/)  ──▶ HydraDB: graph_store.py (OpenCypher HTTP) / managed SDK
question (text or voice/ → STT) ──▶ memory_search.search_events + HydraDB retrieval
   ──▶ temporal state engine (compute_state) ──▶ LLM answer (JSON, evidence ids)
   ──▶ optional re-examination of retained photos ──▶ answer (+ TTS) ──▶ pending actions ──▶ Approve/Dismiss
```
| File | Role |
|---|---|
| `app.py` | Streamlit app: UI, vision pipeline, `ask()`, actions, temporal engine |
| `memory_search.py` | Pure retrieval + Auto Memory change detection (unit-tested) |
| `graph_store.py` | Self-hosted HydraDB adapter. One `(:Observation)` node per object sighting, plus `NEXT_SEEN` edges |
| `voice.py`, `voice/` | OpenAI speech-to-text / text-to-speech, push-to-talk component |
| `camera/` | Live camera component (manual + Auto Memory sampling, HUD, Stop) |
| `scene.py` | Rendered frames for simulated demo memories |

## Tests
| Suite | What it proves | Real or mocked |
|---|---|---|
| `python -m unittest discover -s tests -v` (**26 tests**) | Temporal engine (the 6 original + compatibility), open-ended retrieval, Auto Memory duplicate suppression and change detection, HydraDB adapter write/read/idempotency/auth, voice helpers | **Offline.** The HydraDB adapter runs against `tests/mock_hydradb.py`, a mock that enforces the documented OpenCypher subset |
| `tests/e2e_browser.py` (**11 checks**) | Real headless browser with a fake camera and mic covering camera → Python → memory, Auto Memory (duplicate suppressed, change stored, HUD stop), photo re-examination, push-to-talk → transcript → spoken answer, all views, mobile | **Model stubbed** (`tests/stubs/openai.py`). It proves the wiring, not model quality |
| `python graph_store.py --selftest` | Real write → idempotent re-write → read against your HydraDB server | **Real database** |
| Settings → Agent eval | Five grounded questions answered by your real model | **Real model** |

Last results on this branch: unit 26/26 · E2E 11/11 · state engine 6/6. Real-server and real-model checks are run on the developer's machine. The graph adapter is unchanged from the version whose self-test passed against a live HydraDB node.

## Privacy & limits
- Nothing is analyzed until you capture, or until you enable Auto Memory and start the camera. No video is stored.
- Photos and memories stay in `images/` and `events.json`, which are git-ignored. Only frames sent for analysis go to your model provider.
- The vision prompt describes what's visible and does not identify people.
- Search is keyword search over recorded text. Photos aren't embedded, which is why Ask re-examines photos when needed.
- Counts and colors come from the vision model and can be wrong. Answers show their evidence so you can check.
