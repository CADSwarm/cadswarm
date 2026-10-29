"""Whether a design is judged on how it looks or on what it does.

The reviewer and the MCP tools use this to decide whether a part is allowed to be made of flat
faces. A bracket, a plate and a table are boxes because a box is the honest shape. A car body, a
helmet, a fairing or a product exterior are not: flat faces there read as a block model.

A plain score over a curated vocabulary, never a model call: it must work offline, be explainable
to the person it is judging, and never punish a box for being a box.
"""
import re

# What is judged on form. Whole words in the request, and anywhere in a part's name.
AESTHETIC = frozenset("""
aerodynamic aesthetic automotive boat bodywork bonnet bumper camera car cars chair concept coupe
curvy curved dashboard diffuser ergonomic exterior fairing fascia fender figurine furniture
fuselage grille headlamp headlight helmet hood hull impeller interior jet kettle lamp lifelike
mirror motorbike motorcycle mouse organic phone pillar propeller realistic roof scooter seat sedan
sleek smooth sofa spoiler statue suv tablet taillamp taillight toy truck turbine van vase vehicle
watch wing yacht
""".split())
# What is judged on function.
MECHANICAL = frozenset("""
adapter beam bearing board bolt boss box bracket bushing cabinet case chassis clamp coupler desk
enclosure fastener fixture flange frame gusset heatsink hinge housing jig knob motor mount mounts
nut pcb plate profile pulley rail rib shaft shelf spacer standoff table tube washer
""".split())
# A vehicle's machined and structural parts: a car brief must not ask a brake rotor to be organic.
COMPONENTS = frozenset("""
arm axle brake caliper damper disc driveshaft exhaust floor hub knuckle muffler pan pin rotor spring
strut subframe suspension tank
""".split())
# A make or model is a design brief, whatever else the sentence says.
BRANDS = frozenset("""
apple audi bmw corvette ferrari ford honda iphone is350 lamborghini lexus mazda mclaren mercedes
mustang nissan porsche subaru tesla toyota
""".split())

# A shape judged on form may have at most this share of flat faces, and at least this share of its
# faces curved (with a floor of two so a small glass panel is not asked for a car body's curvature).
MAX_PLANAR_SHARE = 0.6
MIN_CURVED_FACES = 2
MIN_CURVED_SHARE = 0.10


def _words(text):
    # CamelCase part names read as words: RotorFL is "rotor fl", not "rotorfl".
    return set(re.findall(r"[a-z0-9]+", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(text or "")).lower()))


def part_intent(name, default):
    """A part named for a machined or structural component is judged on function even in a car brief
    (review round 2 sent brake rotors and calipers back for being flat); anything else keeps `default`."""
    words = _words(name)
    if words & (MECHANICAL | COMPONENTS) and not words & AESTHETIC:
        return "mechanical"
    return default


def _hits(words, vocabulary):
    return sorted(words & vocabulary)


def required(intent, faces=0):
    """The bar a part of this intent must clear, or None when it is allowed to be boxy."""
    if intent != "aesthetic":
        return None
    return {"max_planar_share": MAX_PLANAR_SHARE,
            "min_curved_faces": max(MIN_CURVED_FACES, round(MIN_CURVED_SHARE * max(0, int(faces or 0))))}


def classify(text="", names=(), type_ids=()):
    """Which vocabulary the request and the part names support, and by how much.

    A part's own name is evidence (`BodyShell`, `Roof`, `Fairing`), and a brand or model name is a
    design brief. The stronger vocabulary wins; a tie or no evidence is mechanical.
    """
    words = _words(text)
    labels = _words(" ".join([*(names or ()), *(type_ids or ())]).replace("_", " "))
    aesthetic = set(_hits(words, AESTHETIC)) | set(_hits(labels, AESTHETIC))
    mechanical = set(_hits(words, MECHANICAL)) | set(_hits(labels, MECHANICAL))
    aesthetic |= set(_hits(words | labels, BRANDS))
    a, m = len(aesthetic), len(mechanical)
    intent = "aesthetic" if a > m else "mechanical"
    confidence = round(abs(a - m) / (a + m), 2) if a + m else 0.0
    return {"intent": intent, "confidence": confidence,
            "matched": sorted(aesthetic if intent == "aesthetic" else mechanical),
            "required": required(intent)}


def assess(stats, intent, bar=None):
    """The verdict and the fix for one part's face mix, in words a person would use."""
    faces = int(stats.get("faces") or 0)
    bar = required(intent, faces) if bar is None else bar
    out = {"intent": intent, "required": bar}
    if not faces:
        return {**out, "verdict": "unknown", "advice": []}
    planar_share = stats.get("planar", 0) / faces
    out["planar_share"] = round(planar_share, 3)
    if bar is None:
        return {**out, "verdict": "not required", "advice": []}
    problems = []
    if planar_share > bar["max_planar_share"]:
        problems.append(
            f"{stats.get('planar', 0)} of {faces} faces are flat ({planar_share:.0%}): this reads as a block "
            f"model. Build the main surfaces as lofts or sweeps (Part.makeLoft across 20+ stations) and keep "
            f"boxes out of the shell.")
    if stats.get("curved", 0) < bar["min_curved_faces"]:
        problems.append(
            f"Only {stats.get('curved', 0)} curved faces: an organic shape needs real curvature, not a "
            f"chamfer on a box.")
    if stats.get("valid") is False:
        problems.append("The shape is not a valid solid; fix that before judging its form.")
    return {**out, "verdict": "fails" if problems else "pass", "advice": problems}
