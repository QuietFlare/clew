"""
Clew core — questions you can ask any graph.

Everything here reads the common schema and nothing else: `tasks`,
`edges`, `outputs`. No engine, no domain.

These lived in a domains/ adapter, re-exported unchanged by every other
one. That placement had a cost: reaching them at all meant going through
an adapter, and paying for its inputs. A domain decides what a SUBJECT
is; that is the whole of its job.
"""

import json
import re
from pathlib import Path

# A tag that is a content hash rather than a version: Wave multi-tool
# images and mulled containers tag with hex. Read before any `-` build
# suffix, so "27211b8c...-0" is a hash and "1.21--h50ea8bc_0" a version.
HEX_TAG = re.compile(r"^[0-9a-f]{12,}$")
# Where a name-version string splits: the first `-` followed by a digit.
# Singularity cache names and bare "toolkit-2.1" strings take this shape.
NAME_DASH_VERSION = re.compile(r"^(.*?)-(\d.*)$")
MATCH_EXACT = "exact"
MATCH_NAME_ONLY = "name-only"


def parse_image(image):
    """
    (name, components, version) of a container reference, version None
    when the reference carries none.

    Forms read: registry/path/name:tag, name@sha256:..., Wave images
    (tools joined by `_`, hash tag), Singularity cache names (`:` and `/`
    turned to `-`, `.img` or `.sif`), conda@hash, and a bare name-version.
    Components are the pieces a needle can name: the whole name plus its
    `_` and `-` parts, so "samtools" finds "bwa_htslib_samtools".
    """
    text = (image or "").strip()
    for scheme in ("docker://", "oras://", "library://", "shub://"):
        text = text.removeprefix(scheme)
    # A digest pins content, it names no version: drop it and read the rest.
    text = text.partition("@")[0]
    if text.endswith((".img", ".sif")):
        text = Path(text).name.rsplit(".", 1)[0]
    name = text.rsplit("/", 1)[-1]
    version = None
    if ":" in name:
        name, _, tag = name.rpartition(":")
        version = tag
    else:
        split = NAME_DASH_VERSION.match(name)
        if split:
            name, version = split.group(1), split.group(2)
    if version is not None:
        first = re.split(r"-+", version, maxsplit=1)[0]
        if version == "latest" or HEX_TAG.match(first):
            version = None
    components = {name} | set(re.split(r"[_-]+", name)) - {""}
    return name, components, version


def version_agrees(wanted, have):
    """
    "1.21" agrees with "1.21" and "1.21--build_0" but not with "1.2".
    A build suffix is not a version, so a prefix ending at a separator
    is enough.
    """
    if have == wanted:
        return True
    return have.startswith(wanted) and not have[len(wanted)].isdigit()


def container_match(needle, image):
    """
    How the needle matches the image: MATCH_EXACT, MATCH_NAME_ONLY when the
    needle carries a version the image cannot confirm, None otherwise.
    """
    want, _, want_version = parse_image(needle)
    if not want:
        return None
    _, components, have_version = parse_image(image)
    if want not in components:
        return None
    if not want_version:
        return MATCH_EXACT
    if have_version is None:
        return MATCH_NAME_ONLY
    return MATCH_EXACT if version_agrees(want_version, have_version) else None


def container_matches(graph, needle):
    """{task hash: how it matched} for every task whose container matches."""
    found = {}
    for task_hash, task in graph["tasks"].items():
        how = container_match(needle, task.get("container") or "")
        if how:
            found[task_hash] = how
    return found


def container_entry_nodes(graph, needle):
    """
    Entry nodes for a tool-defect trigger: tasks whose container matches.
    Name-only matches are included; container_matches says which.
    """
    return {f"container:{needle}": sorted(container_matches(graph, needle))}


def external_input_matches(graph, filename):
    """
    {basename: [consumer]} over EXTERNAL inputs the name reaches: the exact
    basename, and companions named `<filename>.<ext>`, since an index or
    dictionary is regenerated with the file it indexes. A directory input
    matches by its own basename.
    """
    found = {}
    for edge in graph["edges"]:
        if edge["producer"] != "EXTERNAL":
            continue
        name = Path(edge["filename"]).name
        if name == filename or name.startswith(filename + "."):
            found.setdefault(name, set()).add(edge["consumer"])
    return {name: sorted(nodes) for name, nodes in sorted(found.items())}


def external_input_entry_nodes(graph, filename):
    """
    Entry nodes for an upstream-input trigger: tasks that consumed an
    EXTERNAL file with this basename or a companion of it. This is the
    reference-update / load-bearing-input case.
    """
    nodes = set()
    for consumers in external_input_matches(graph, filename).values():
        nodes.update(consumers)
    return {f"input:{filename}": sorted(nodes)}


def load_assertions(path):
    """
    Externally-asserted facts the pipeline cannot know about itself
    (publication, so far). Returns {task_hash: assertion_record}; a missing
    path honestly means "publication status unknown".
    """
    if not path:
        return {}
    data = json.loads(Path(path).read_text())
    return {rec["task"]: rec for rec in data.get("published", [])}


def outputs_for(graph, task_hashes):
    """Every file produced by the given tasks, as 'hash/filename'."""
    files = []
    for task_hash in sorted(task_hashes):
        for filename in graph["outputs"].get(task_hash, []):
            files.append(f"{task_hash}/{filename}")
    return files


def describe(graph, task_hash):
    """Short human label for a task: the process name without its full path."""
    task = graph["tasks"].get(task_hash, {})
    return (task.get("process", "") or "?").split(":")[-1]


def hashed_layout(task):
    """
    Whether the recorded workdir ends in '<prefix>/<hash...>' for this
    task's own 'prefix/hash' id: the hashed work-tree layout.
    Graphs extracted before `workpath` existed carry only this clue.
    """
    parts = Path(task.get("workdir") or "").parts
    head, sep, rest = (task.get("hash") or "").partition("/")
    return (sep == "/" and "/" not in rest and len(parts) >= 2
            and parts[-2] == head and parts[-1].startswith(rest))


def relative_to(path, root):
    """
    `path` under `root` as a string, or None when it is not under it. On
    strings rather than Path so recorded cloud URIs work the same way.
    """
    if not path or not root:
        return None
    root = root.rstrip("/") + "/"
    if not path.startswith(root) or len(path) == len(root):
        return None
    return path[len(root):]


def local_workdir(task, work_root):
    """
    The task directory under work_root, or None when the graph does not
    say where under the root the task ran.

    `workpath` is the extractor's answer, relative to the engine's root, so
    a graph from another host still resolves. Without it the recorded
    absolute path is only trusted when it visibly follows the hashed
    layout; any other shape (one directory shared by every task, a nested
    call tree) would resolve to somewhere wrong and read as DESTROYED or,
    worse, as deletable.
    """
    if not work_root:
        return None
    workpath = task.get("workpath")
    if workpath:
        return Path(work_root, workpath)
    if hashed_layout(task):
        return Path(work_root, *Path(task["workdir"]).parts[-2:])
    return None


def resolve_workdirs(graph, work_root):
    """
    {task hash: local directory or None} for every task, plus warnings.

    Two refusals, both in the direction of not claiming anything: tasks
    that resolve to one shared directory are all unresolved, because a
    verdict on the directory would be a verdict on every one of them; and
    when no resolved directory exists at all the root is more likely wrong
    than the whole run gone, so every task is unresolved rather than
    DESTROYED.
    """
    resolved = {h: local_workdir(t, work_root) for h, t in graph["tasks"].items()}
    warnings = []
    if not work_root:
        return resolved, warnings

    owners = {}
    for task_hash, path in resolved.items():
        if path is not None:
            owners.setdefault(path, []).append(task_hash)
    shared = {path: hashes for path, hashes in owners.items() if len(hashes) > 1}
    for path, hashes in sorted(shared.items()):
        for task_hash in hashes:
            resolved[task_hash] = None
    if shared:
        count = sum(len(h) for h in shared.values())
        warnings.append(
            f"{count} tasks resolve to {len(shared)} shared director"
            f"{'y' if len(shared) == 1 else 'ies'} under {work_root}; "
            "storage left unchecked for them, one directory cannot answer "
            "for several tasks")

    candidates = [p for p in resolved.values() if p is not None]
    if candidates and not any(p.is_dir() for p in candidates):
        for task_hash in resolved:
            resolved[task_hash] = None
        warnings.append(
            f"none of {len(candidates)} recorded task directories exists under "
            f"{work_root}; the root looks wrong, storage left unchecked")
    return resolved, warnings


def output_digests(graph):
    """digest -> [(task, file)] over every output that carries one."""
    index = {}
    for task_hash, details in graph.get("output_details", {}).items():
        for detail in details:
            digest = detail.get("digest")
            if digest:
                index.setdefault(digest, []).append((task_hash, detail["file"]))
    return index


def published_digests(graph):
    """digest -> [published relative path], from a `clew digest --results` pass."""
    index = {}
    for rel, entry in graph.get("published", {}).items():
        digest = entry.get("digest")
        if digest:
            index.setdefault(digest, []).append(rel)
    return index


STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_CACHED = "CACHED"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_UNRECORDED = ""
STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_CACHED, STATUS_UNKNOWN,
            STATUS_UNRECORDED)

# Each engine's own words for the four states. A word not listed here maps
# to UNKNOWN, never to FAILED: a running or queued task must not read as a
# failed one, because a failed one is proposed for deletion.
ENGINE_STATUS_WORDS = {
    STATUS_COMPLETED: ("COMPLETED", "DONE", "SUCCEEDED", "SUCCESS", "FINISHED"),
    STATUS_FAILED: ("FAILED", "FAILURE", "ABORTED", "TERMINATED", "INCOMPLETE",
                    "TIMED_OUT", "RETRYABLEFAILURE", "ERROR"),
    STATUS_CACHED: ("CACHED", "SKIPPED"),
}


def task_status(engine_word):
    """The engine's status word in the closed vocabulary above."""
    word = (engine_word or "").strip().upper()
    if not word:
        return STATUS_UNRECORDED
    for status, words in ENGINE_STATUS_WORDS.items():
        if word in words:
            return status
    return STATUS_UNKNOWN


TASK_FIELDS = ("hash", "name", "process", "container", "status",
               "script", "workdir")
# task_id: the engine's own counter, never a key. target: where the task
# ran. workpath: the task directory relative to the engine's root, in the
# engine's own layout ('xx/hash' for a hashed work tree, 'call-x/shard-0'
# for a call tree), so --work-root plus workpath is the directory on this
# machine. Absent when the engine has no per-task directory.
OPTIONAL_TASK_FIELDS = ("task_id", "target", "workpath")
EDGE_FIELDS = ("consumer", "producer", "filename", "target")
EXTERNAL = "EXTERNAL"


def contract_violations(graph):
    """
    Everything downstream assumes this shape. Returns a list of problems,
    empty when the graph conforms. Extra keys are allowed.
    """
    problems = []
    for key, kind in (("tasks", dict), ("edges", list), ("outputs", dict)):
        if not isinstance(graph.get(key), kind):
            problems.append(f"{key}: missing or not a {kind.__name__}")
    if problems:
        return problems

    tasks = graph["tasks"]
    for key, task in tasks.items():
        if not isinstance(task, dict):
            problems.append(f"task {key}: not an object")
            continue
        for field in TASK_FIELDS:
            if not isinstance(task.get(field), str):
                problems.append(f"task {key}: {field} missing or not a string")
        for field in OPTIONAL_TASK_FIELDS:
            if task.get(field) is not None and not isinstance(task[field], (str, int)):
                problems.append(f"task {key}: {field} is not a string or integer")
        workpath = task.get("workpath")
        if isinstance(workpath, str) and (
                workpath.startswith("/") or ".." in Path(workpath).parts):
            problems.append(f"task {key}: workpath must be relative and stay under the root")
        if task.get("hash") != key:
            problems.append(f"task {key}: hash field does not match its key")
        status = task.get("status")
        if isinstance(status, str) and status != status.upper():
            problems.append(f"task {key}: status {status!r} is not upper-case")
        labels = task.get("labels")
        if labels is not None and not (
                isinstance(labels, dict)
                and all(isinstance(k, str) and isinstance(v, str)
                        for k, v in labels.items())):
            problems.append(f"task {key}: labels must map strings to strings")

    for i, edge in enumerate(graph["edges"]):
        if not isinstance(edge, dict):
            problems.append(f"edge {i}: not an object")
            continue
        for field in EDGE_FIELDS:
            if not isinstance(edge.get(field), str):
                problems.append(f"edge {i}: {field} missing or not a string")
        consumer, producer = edge.get("consumer"), edge.get("producer")
        if consumer not in tasks:
            problems.append(f"edge {i}: consumer {consumer!r} is not a task")
        if producer != EXTERNAL and producer not in tasks:
            problems.append(f"edge {i}: producer {producer!r} is not a task")
        if consumer == producer:
            problems.append(f"edge {i}: task {consumer!r} feeds itself")

    for key, names in graph["outputs"].items():
        if key not in tasks:
            problems.append(f"outputs: {key!r} is not a task")
        if not (isinstance(names, list)
                and all(isinstance(n, str) for n in names)):
            problems.append(f"outputs {key}: not a list of strings")

    def bad_digest(value):
        return value is not None and not (
            isinstance(value, str) and ":" in value and value.split(":", 1)[1])

    for key, details in (graph.get("output_details") or {}).items():
        if key not in tasks:
            problems.append(f"output_details: {key!r} is not a task")
        for detail in details if isinstance(details, list) else []:
            if not isinstance(detail, dict) or not isinstance(detail.get("file"), str):
                problems.append(f"output_details {key}: entry without a file name")
            elif bad_digest(detail.get("digest")):
                problems.append(f"output_details {key}/{detail['file']}: "
                                "digest is not '<algorithm>:<value>'")
    for i, edge in enumerate(graph["edges"]):
        if isinstance(edge, dict) and bad_digest(edge.get("digest")):
            problems.append(f"edge {i}: digest is not '<algorithm>:<value>'")
    for rel, entry in (graph.get("published") or {}).items():
        if not isinstance(entry, dict) or bad_digest(entry.get("digest")):
            problems.append(f"published {rel}: digest is not '<algorithm>:<value>'")
    return problems
