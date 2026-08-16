"""Role candidate extraction from industrial clauses."""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from robot_intent_agent.schemas.semantic_task_graph import EvidenceSpan, SemanticEntity
from robot_intent_agent.domain.industrial_ontology import match_industrial_templates


COLORS = {
    "红色": "red", "蓝色": "blue", "绿色": "green", "黄色": "yellow",
    "白色": "white", "黑色": "black", "透明": "transparent",
    "红": "red", "蓝": "blue", "绿": "green", "黄": "yellow",
    "白": "white", "黑": "black",
}
MATERIALS = {"玻璃": "glass", "塑料": "plastic", "金属": "metal", "木质": "wood", "橡胶": "rubber"}
CATEGORIES = {
    "杯子": "cup", "杯": "cup", "水杯": "cup", "药瓶": "medicine_bottle", "瓶子": "bottle", "瓶": "bottle",
    "工件": "workpiece", "零件": "part", "物体": "object", "东西": "object",
    "夹具": "fixture", "检测区": "inspection_zone", "检验区": "inspection_zone",
    "料箱": "parts_bin", "周转箱": "parts_bin", "工位": "workbench", "工作台": "workbench",
    "托盘": "tray", "桌子": "table", "桌": "table", "操作员": "operator", "用户": "human", "我": "human",
    "传送带": "conveyor", "夹具": "fixture",
    "方块": "block", "积木": "block", "小球": "ball", "球": "ball",
    # Strict acceptance vocabulary. Grounding still owns final identity; these
    # aliases only prevent a valid mention from becoming an empty role.
    "盒子": "box", "箱子": "box", "收纳箱": "bin", "柜子": "cabinet",
    "书": "book", "书本": "book", "轴承": "bearing", "齿轮": "gear",
    "组件": "component", "玻璃": "glass", "玻璃杯": "glass",
    "花瓶": "vase", "水壶": "hot_kettle", "热水壶": "hot_kettle",
    "热表面": "hot_surface", "焊接区": "welding_zone",
    "高温台": "hot_surface", "高温工作台": "hot_surface",
    "料箱": "parts_bin", "料斗": "bin", "工作台": "workbench",
    "cube": "cube", "block": "block", "ball": "ball", "cup": "cup",
    "box": "box", "table": "table", "object": "object", "thing": "object",
    "anything else": "object",
}


def _entity(local_ref: str, mention: str, category: Optional[str], text: str, start: int,
            attrs: Optional[Dict[str, object]] = None, relation: Optional[str] = None) -> SemanticEntity:
    evidence = [mention]
    if relation:
        evidence.append(relation)
    from robot_intent_agent.schemas.semantic_task_graph import SpatialConstraint
    spatial = [SpatialConstraint(relation=relation, reference="robot_base", evidence=[relation])] if relation else []
    return SemanticEntity(local_ref=local_ref, mention=mention, category=category,
                          attributes=attrs or {}, spatial_constraints=spatial,
                          evidence_spans=evidence,
                          evidence=[EvidenceSpan(value=mention, source_text=text, start=start,
                                                 end=start + len(mention), confidence=0.9,
                                                 rule_id="role.mention")])


def _find_mention(text: str, category: Optional[str] = None, after: int = 0) -> Optional[tuple[str, int, Optional[str], Dict[str, object]]]:
    ordered = sorted(CATEGORIES.items(), key=lambda item: len(item[0]), reverse=True)
    matches = []
    for surface, normalized in ordered:
        if normalized != category and category is not None:
            continue
        index = text.find(surface, after)
        if index < 0:
            continue
        matches.append((index, -len(surface), surface, normalized))
    if not matches:
        return None
    _, _, surface, normalized = sorted(matches)[0]
    index = text.find(surface, after)
    start = index
    # Keep the complete immediately preceding descriptor chain.  The old
    # implementation retained only one short adjective (for example
    # ``白色的药瓶``), which discarded the scene evidence in expressions such
    # as ``靠近左侧边缘的中等大小的白色药瓶``.  Grounding is the component
    # that interprets these descriptors, so role extraction must preserve
    # them instead of trying to resolve the object here.
    prefix_start = start
    clause_start = max(
        text.rfind(mark, 0, start) + 1
        for mark in ("，", "。", "；", ",", ";")
    )
    descriptor_scope = text[clause_start:start]
    descriptor_atom = (
        r"(?:靠近[^，。；,;]{1,24}?的|位于[^，。；,;]{1,24}?的|"
        r"操作区前方的|操作区后方的|前方的|后方的|"
        r"中等大小的|尺寸较大(?:的)?|尺寸较小(?:的)?|"
        r"偏小的|偏大的|小型的|大型的|矮胖的|短粗的|细长的|长条的|"
        r"最左边的|最右边的|左侧的|右侧的|左边的|右边的|"
        r"前面的|后面的|中间的|"
        r"(?:红色|蓝色|绿色|黄色|白色|黑色|透明|红|蓝|绿|黄|白|黑)色?的?|"
        r"(?:玻璃|塑料|金属|木质|橡胶)的?)"
    )
    # Imperative markers can occur between a scene prefix and its noun:
    # ``靠近左侧边缘的中等大小的把白色药瓶...``.  Treat those markers as
    # separators inside the descriptor chain so the original character
    # offsets remain valid.
    descriptor_separator = (
        r"(?:\s*(?:把|将)\s*|"
        r"\s*让[^，。；,;]{0,24}?\s*)"
    )
    descriptor_chain = re.search(
        rf"(?:{descriptor_atom})(?:(?:{descriptor_separator})?(?:{descriptor_atom}))*$",
        descriptor_scope,
    )
    if descriptor_chain:
        prefix_start = clause_start + descriptor_chain.start()
    else:
        # Preserve the compact legacy forms when they are not covered by the
        # wider chain (including an attribute without ``的``).
        for descriptor in (list(COLORS) + list(MATERIALS) +
                           ["最左边的", "最右边的", "左边的", "右边的", "前面的", "后面的", "最大的", "最小的"]):
            if start >= len(descriptor) + 1 and text[start-len(descriptor)-1:start] == descriptor + "的":
                prefix_start = start-len(descriptor)-1
                break
            if start >= len(descriptor) and text[start-len(descriptor):start] == descriptor:
                prefix_start = start-len(descriptor)
                break
    mention = text[prefix_start:index + len(surface)]
    attrs: Dict[str, object] = {}
    for cn, value in COLORS.items():
        if cn in mention:
            attrs["color"] = value
    for cn, value in MATERIALS.items():
        if cn in mention:
            attrs["material"] = value
    if any(token in mention for token in ("偏小", "较小", "小型", "尺寸较小")):
        attrs["size"] = "small"
    elif any(token in mention for token in ("偏大", "较大", "大型", "尺寸较大")):
        attrs["size"] = "large"
    elif any(token in mention for token in ("中等大小", "适中")):
        attrs["size"] = "medium"
    if any(token in mention for token in ("细长", "长条")):
        attrs["shape"] = "elongated"
    elif any(token in mention for token in ("矮胖", "短粗")):
        attrs["shape"] = "compact"
    relation = None
    for cue, rel in (("最左", "LEFTMOST"), ("最右", "RIGHTMOST"), ("左边", "LEFT"), ("右边", "RIGHT"),
                     ("前面", "FRONT"), ("后面", "BEHIND"), ("中间", "MIDDLE")):
        if cue in mention:
            relation = rel
            break
    return mention, prefix_start, normalized, attrs


def _apply_attribute_only_mention(hit: Optional[tuple[str, int, Optional[str], Dict[str, object]]],
                                  text: str, start: int = 0):
    """Attach attributes stated outside the compact noun span.

    Expressions such as “颜色为红色的药瓶” and “红色的杯子” can be
    truncated by the noun lookup.  The parser must preserve those attributes
    for grounding; identity remains scene-owned.
    """
    if not hit:
        return hit
    mention, hit_start, category, attrs = hit
    # Some role patterns search a captured suffix and return an offset local
    # to that suffix. Rebase such offsets against the original instruction
    # before applying nearby attributes; otherwise a preceding target clause
    # can be mistaken for the obstacle's modifier.
    if not text[hit_start:hit_start + len(mention)] == mention:
        actual_start = text.find(mention, max(0, hit_start))
        if actual_start >= 0:
            hit_start = actual_start
    prefix = text[max(0, hit_start - 16):hit_start]
    # Attributes do not cross clause boundaries.  Without this scope cut,
    # "抓黄色书，路径别经过玻璃杯" incorrectly assigns yellow to the
    # obstacle because the noun lookup sees the preceding target phrase.
    prefix = re.split(r"[，。；,;]", prefix)[-1]
    merged = dict(attrs or {})
    color_pattern = "|".join(sorted((re.escape(item) for item in COLORS), key=len, reverse=True))
    material_pattern = "|".join(sorted((re.escape(item) for item in MATERIALS), key=len, reverse=True))
    color_match = re.search(rf"(?:颜色(?:为|是)\s*)?({color_pattern})的?\s*$", prefix)
    material_match = re.search(rf"(?:材质(?:为|是)\s*)?({material_pattern})的?\s*$", prefix)
    if color_match:
        merged["color"] = COLORS[color_match.group(1)]
    if material_match:
        merged["material"] = MATERIALS[material_match.group(1)]
    # “颜色为红色的药瓶” puts the attribute farther from the noun than the
    # compact prefix check, but it is still a same-phrase modifier.
    color_phrase = re.search(rf"颜色(?:为|是)\s*({color_pattern})\s*的?\s*$", prefix)
    if color_phrase:
        merged["color"] = COLORS[color_phrase.group(1)]
    material_phrase = re.search(rf"材质(?:为|是)\s*({material_pattern})\s*的?\s*$", prefix)
    if material_phrase:
        merged["material"] = MATERIALS[material_phrase.group(1)]
    return mention, hit_start, category, merged


def parse_roles(instruction: str, actions: List[str]) -> tuple[List[SemanticEntity], Dict[str, str]]:
    text = instruction or ""
    entities: List[SemanticEntity] = []
    role_refs: Dict[str, str] = {}
    seen: Dict[tuple[str, str], str] = {}
    vague_peer_avoidance = bool(re.search(
        r"(?:旁边的|附近的|周围的)?(?:同类物体|同类对象|同样的物体|同类目标)",
        text,
    ))

    def add(role: str, hit: Optional[tuple[str, int, Optional[str], Dict[str, object]]]):
        if not hit:
            return
        hit = _apply_attribute_only_mention(hit, text)
        mention, start, category, attrs = hit
        key = (role, mention)
        local_ref = seen.get(key)
        if local_ref is None:
            local_ref = f"entity-{len(entities) + 1}"
            seen[key] = local_ref
            relation = next((rel for cue, rel in (("最左", "LEFTMOST"), ("最右", "RIGHTMOST"), ("左边", "LEFT"), ("右边", "RIGHT"), ("前面", "FRONT"), ("后面", "BEHIND"), ("中间", "MIDDLE")) if cue in mention), None)
            entities.append(_entity(local_ref, mention, category, text, start, attrs, relation))
        role_refs[role] = local_ref

    def add_open(role: str, mention: str, start: int = 0, attrs: Optional[Dict[str, object]] = None):
        mention = (mention or "").strip(" ，,。；;")
        if not mention or role in role_refs:
            return
        local_ref = f"entity-{len(entities) + 1}"
        entities.append(_entity(local_ref, mention, "object", text, start, attrs or {}, None))
        role_refs[role] = local_ref

    # Explicit role markers are authoritative over generic synonyms.
    destination_match = re.search(
        r"(?:至|放到|放入|放进|放在|摆放在|摆到|置于|移到|移送到|移送至|搬运到|搬运至|转移到|转移至|送到|送回|装入|收入|归入|安置到|转交到|转送到|改送到|引入|灌到|上料到|倒入|倒进|倾倒|倾入|注入|堆到|叠到|码放在)"
        r"\s*([^，。；,;]+)", text
    )
    if destination_match:
        add("destination", _find_mention(destination_match.group(1), after=0))
    # FETCH/TRANSFER phrasing often places the destination before the target
    # description: "取到收取区" / "带回接收位".  These are delivery roles,
    # not generic object mentions.  Keep the extraction evidence-based and
    # let GroundingEngine bind the described zone to a scene entity.
    fetch_destination = re.search(
        r"(?:取到|拿到|带到|带回|送回|弄到|取回来|拿回来)\s*([^，。；,;]+?)(?=(?:，|。|；|,|;|$))",
        text,
    )
    if fetch_destination and "destination" not in role_refs:
        destination_text = fetch_destination.group(1).strip()
        # Search the captured phrase itself. The captured substring has its
        # own coordinate system; using the original absolute offset makes a
        # valid noun such as “送回托盘” look like an unresolved object.
        destination_hit = _find_mention(destination_text)
        if destination_hit:
            add("destination", destination_hit)
        elif not any(cue in destination_text for cue in ("机器人身边", "这边", "那里", "现场")):
            add_open("destination", destination_text, fetch_destination.start(1))

    # A delivery command may put the object before the retrieval verb, as in
    # “把蓝色盒子取回收纳箱”.  Do this before the post-verb FETCH pattern so
    # the receiving bin cannot be mistaken for the manipulated object.
    if "FETCH" in actions and "theme" not in role_refs:
        fetch_theme_before = re.search(
            r"(?:把|将)\s*(?P<theme>[^，。；,;]+?)\s*(?:取回|带回|拿回|带到)"
            r"(?=[^，。；,;]*(?:收纳箱|接收|收取|回收|托盘|机器人|身边|这边))",
            text,
        )
        if fetch_theme_before:
            add("theme", _find_mention(text, after=fetch_theme_before.start("theme")))

    # FETCH often expresses the source and manipulated object together:
    # “从桌面取出红色盒子送回托盘”.  The generic source/theme fallback can
    # otherwise bind the table twice and lose the box.  Extract the object
    # after the retrieval verb as a semantic theme; grounding still chooses
    # the scene-owned id.
    if "FETCH" in actions and "theme" not in role_refs:
        fetch_theme = re.search(
            r"(?:取出|取回|拿回|带回|拿来|带来)\s*(?P<theme>[^，。；,;]+?)"
            r"(?=(?:送回|带到|送到|放到|放入|交给|，|。|；|,|;|$))",
            text,
        )
        if fetch_theme:
            add("theme", _find_mention(text, after=fetch_theme.start("theme")))
    # “向托盘倾倒” is a POUR destination, while “向托盘方向推动” is only
    # a PUSH direction and must not become a physical destination role.
    pour_destination_match = re.search(
        r"向\s*([^，。；,;]+?)\s*(?=(?:倾倒|倒入|倒进|注入|加料))", text
    )
    if pour_destination_match and "destination" not in role_refs:
        add("destination", _find_mention(pour_destination_match.group(1), after=0))
    source_match = re.search(r"从([^，。；,;]+?)(?:移|搬|转|送|运)", text)
    if source_match:
        add("source", _find_mention(source_match.group(1), after=0))

    # Direction-first POUR clauses place the destination before the source,
    # e.g. ``向托盘倾空黄色书本``. Extract both roles together so the generic
    # theme fallback cannot mistake the destination tray for the pourable
    # object.
    if "POUR" in actions:
        directed_pour = re.search(
            r"(?:向|朝向|倒向|倾向)\s*(?P<destination>[^，。；,;]+?)\s*"
            r"(?P<verb>倾空|倾倒|倒入|倒进|注入|灌入|灌进|导入|转注到)\s*"
            r"(?P<theme>[^，。；,;]+)",
            text,
        )
        if directed_pour:
            add("destination", _find_mention(directed_pour.group("destination"), after=0))
            add("theme", _find_mention(directed_pour.group("theme"), after=0))

    # Contents language still refers to the source container: “药瓶的内部
    # 物料” and “书本里的东西” are not new object categories. Preserve the
    # containing object as POUR.theme and leave the material itself to the
    # execution skill template.
    if "POUR" in actions and "theme" not in role_refs:
        contents_theme = re.search(
            r"(?P<theme>[^，。；,;]+?)(?:的内部物料|内部物料|里面的东西|的东西|内容物)",
            text,
        )
        if contents_theme:
            add("theme", _find_mention(text, after=contents_theme.start("theme")))
    if "POUR" in actions and "theme" not in role_refs:
        pour_target = re.search(
            r"(?:对|给)\s*(?P<theme>[^，。；,;]+?)(?:的)?(?:进行)?加料",
            text,
        )
        if pour_target:
            add("theme", _find_mention(text, after=pour_target.start("theme")))
    recipient_match = re.search(r"(?:递交给|递给|交给|递到|交到|送到|给)([^，。；,;]+)", text)
    if recipient_match:
        add("recipient", _find_mention(recipient_match.group(1), after=0))

    # In transfer/handover language, "交给/移交给" can introduce a physical
    # tray or work position.  It is a destination unless the noun is a human
    # recipient.  This role distinction prevents the graph from losing the
    # tray simply because the surface verb is recipient-like.
    if (set(actions) & {"TRANSFER", "PLACE", "STACK"}) and "destination" not in role_refs:
        handoff_surface = re.search(
            r"(?:\u4ea4\u7ed9|\u79fb\u4ea4\u7ed9|\u8f6c\u4ea4\u7ed9|\u5b89\u7f6e\u6210|\u6210\u4e3a)\s*([^，。；,;]+)",
            text,
        )
        if handoff_surface:
            hit = _find_mention(handoff_surface.group(1), after=0)
            if hit and hit[2] not in {"human", "operator"}:
                mention, local_start, category, attrs = hit
                add("destination", (mention, handoff_surface.start(1) + local_start, category, attrs))
            elif not hit:
                add_open("destination", handoff_surface.group(1), handoff_surface.start(1))

    # STACK phrases such as ``和托盘叠合起来`` name the support object before
    # the stacking verb. Capture that pair before generic destination rules,
    # whose verb list could otherwise bind the trailing word ``起来``.
    if "STACK" in actions and "destination" not in role_refs:
        stack_pair = re.search(
            r"(?:与|和|跟)\s*(?P<destination>[^，。；,;]+?)\s*"
            r"(?=(?:上下相叠|叠合|叠放|形成堆叠))",
            text,
        )
        if stack_pair:
            add("destination", _find_mention(stack_pair.group("destination"), after=0))

    # A physical tray/position introduced by "交给" is not a human
    # recipient. Remove the recipient interpretation when the same clause
    # clearly names a scene surface.
    if "destination" in role_refs and "recipient" in role_refs:
        destination_entity = next((item for item in entities if item.local_ref == role_refs["destination"]), None)
        if destination_entity and destination_entity.category not in {"human", "operator"}:
            role_refs.pop("recipient", None)
    # Recipient mentions can use an object-like surface noun (操作员、工作人
    # 员、接收者) that is not part of the compact category map. Preserve it as
    # a normalized recipient entity for deterministic scene grounding.
    if "HANDOVER" in actions and "recipient" not in role_refs:
        recipient_surface = re.search(
            r"(操作员|工作人员|操作人员|接收者|人手|对方|用户)", text,
        )
        if recipient_surface:
            add_open("recipient", recipient_surface.group(1), recipient_surface.start(1), {})

    # "操作员可接取的位置" is a handover zone, not a second recipient
    # object. Prefer the explicit recipient entity and leave the destination
    # role for a physical zone only when the action contract accepts it.
    if "HANDOVER" in actions and "recipient" in role_refs:
        role_refs.pop("destination", None)

    # In FETCH instructions, a phrase such as "带来交给接收位" describes a
    # receiving location, not a human recipient. Keep that distinction in the
    # semantic graph so FETCH can satisfy its destination contract.
    if "FETCH" in actions and "destination" not in role_refs and "recipient" in role_refs:
        recipient_entity = next((item for item in entities
                                  if item.local_ref == role_refs["recipient"]), None)
        mention = recipient_entity.mention if recipient_entity else ""
        if any(cue in text or cue in mention for cue in ("接收", "收取", "回收", "机器人身边", "这边", "指定接收")):
            role_refs["destination"] = role_refs.pop("recipient")

    # Delivery verbs can be separated from the destination by a compound
    # clause ("取回并放到接收区"). Recover that destination without making
    # the action parser responsible for physical scene identity.
    if "FETCH" in actions and "destination" not in role_refs:
        delivery_destination = re.search(
            r"(?:放到|放入|送到|搬到|交给)\s*([^，。；,;]+)", text,
        )
        if delivery_destination:
            destination_text = delivery_destination.group(1).strip()
            hit = _find_mention(destination_text, after=delivery_destination.start(1))
            if hit:
                add("destination", hit)
            else:
                add_open("destination", destination_text, delivery_destination.start(1))

    # A benchmark instruction may put the disambiguating description after
    # the command: "...，目标是中间偏后的偏小的蓝色瓶子".  That clause is
    # authoritative for the theme.  Parse it as a normal entity description
    # and replace an earlier generic mention such as "夹具" or "现场".
    target_description = re.search(
        r"(?:目标是|目标为|指定(?:对象)?是|要找的是|要操作的是)\s*([^，。；,;]+)",
        text,
    )
    if target_description:
        target_text = target_description.group(1).strip()
        target_hit = _find_mention(target_text, after=0)
        if target_hit:
            _, _, category, attrs = target_hit
            described_attrs = dict(attrs or {})
            if any(token in target_text for token in ("偏小", "小型", "较小")) or re.search(r"(?<!大)小的", target_text):
                described_attrs["size"] = "small"
            elif any(token in target_text for token in ("偏大", "大型", "较大")) or re.search(r"(?<!大)大的", target_text):
                described_attrs["size"] = "large"
            elif any(token in target_text for token in ("中等", "适中")):
                described_attrs["size"] = "medium"
            if any(token in target_text for token in ("细长", "长条")):
                described_attrs["shape"] = "elongated"
            elif any(token in target_text for token in ("矮胖", "短粗")):
                described_attrs["shape"] = "compact"
            # Preserve the complete description in the semantic graph. The
            # category remains normalized, while grounding can use the
            # relative size/spatial wording instead of losing it at the noun.
            add("theme", (target_text, target_description.start(1), category, described_attrs))

    # Destination descriptions may be introduced by a directional clause
    # without an imperative verb ("朝向托盘完成倾注", "向托盘释放物料").
    if (set(actions) & {"PLACE", "TRANSFER", "FETCH", "STACK", "POUR"}) and "destination" not in role_refs:
        directional_destination = re.search(
            r"(?:朝向|向|倾向|倒向|对着|到|至|进|入)\s*(托盘|接收区|收取区|接收台|回收托盘|回收位置|作业位|工位)",
            text,
        )
        if directional_destination:
            hit = _find_mention(directional_destination.group(1), after=0)
            if hit:
                mention, local_start, category, attrs = hit
                add("destination", (mention, directional_destination.start(1) + local_start, category, attrs))
            else:
                add_open("destination", directional_destination.group(1), directional_destination.start(1))

    # All supported transfer-like actions use the same destination role.  The
    # verb family is open, while the destination remains scene-grounded.
    if (set(actions) & {"PLACE", "TRANSFER", "FETCH", "STACK", "POUR"}) and "destination" not in role_refs:
        destination_surface = re.search(
            r"(?:在|于|到|向|朝向|倾向|对着|回到|送回)\s*(托盘|接收区|收取区|接收台|回收托盘|回收位置|作业位|工位|机器人接收区)",
            text,
        )
        if destination_surface:
            hit = _find_mention(destination_surface.group(1), after=0)
            if hit:
                mention, local_start, category, attrs = hit
                add("destination", (mention, destination_surface.start(1) + local_start, category, attrs))
            else:
                add_open("destination", destination_surface.group(1), destination_surface.start(1))
        destination_phrase = re.search(
            r"(?:安顿到|安置到|落在|归置进|归入|送到|送回|转送至|转送到|调运到|输送到|改送至|改送到|调拨进|转运到|搬至|转交至|转交到|移送到|"
            r"放到|放入|放进|装入|安放于|归置于|摆放于|堆到|叠到|码到|叠置到|摞到|压在|灌进|灌入|"
            r"注入|倒进|倾入|导入|引入|灌到|转注到|送回|带到|取到)\s*([^，。；,;]+)",
            text,
        )
        if destination_phrase and "destination" not in role_refs:
            destination_text = destination_phrase.group(1).strip()
            destination_hit = _find_mention(destination_text, after=0)
            if destination_hit:
                mention, local_start, category, attrs = destination_hit
                add("destination", (mention, destination_phrase.start(1) + local_start, category, attrs))
            else:
                add_open("destination", destination_text, destination_phrase.start(1))

    # Open surface forms for destinations.  Keep the complete noun phrase so
    # grounding can use a surface qualifier such as "托盘的承载区域", while
    # the scene matcher still binds the contained tray/zone entity.
    if (set(actions) & {"PLACE", "TRANSFER", "FETCH", "STACK", "POUR"}) and "destination" not in role_refs:
        open_destination = re.search(
            r"(?:\u653e\u56de|\u653e\u5230|\u653e\u5165|\u5b89\u987f\u5230|\u5f52\u7f6e\u8fdb|\u6536\u8fdb|\u5c31\u4f4d|\u79fb\u9001\u5230|\u8fd0\u5f80|\u9001\u5165|\u8f93\u9001\u8fdb|\u8c03\u5230|\u53e0\u7f6e\u5230|\u7801\u5230|\u53e0\u5408|\u50be\u6ce8\u5230|\u503e\u5411|\u5012\u8fdb|\u8f6c\u6ce8\u5230|\u5bfc\u5165|\u56de\u5230|\u5e26\u5230|\u53d6\u5230|\u642c\u5230|\u9001\u56de)\s*([^，。；,;]+?)(?=[，。；,;]|$)",
            text,
        )
        if open_destination:
            destination_text = open_destination.group(1).strip()
            hit = _find_mention(destination_text, after=0)
            if hit:
                mention, local_start, category, attrs = hit
                add("destination", (mention, open_destination.start(1) + local_start, category, attrs))
            else:
                add_open("destination", destination_text, open_destination.start(1))

    # STACK has an explicit relation form ("with the tray, one above the
    # other") in which the destination noun is not preceded by a movement
    # verb.  It is still a destination/support surface in the task graph.
    if "STACK" in actions and "destination" not in role_refs:
        stack_surface = re.search(r"(?:\u4e0e|\u548c|\u8ddf)\s*([^，。；,;]+?)\s*(?:\u4e0a\u4e0b\u76f8\u53e0|\u53e0\u5408|\u53e0\u653e|\u5f62\u6210\u5806\u53e0)", text)
        if stack_surface:
            hit = _find_mention(stack_surface.group(1), after=0)
            if hit:
                mention, local_start, category, attrs = hit
                add("destination", (mention, stack_surface.start(1) + local_start, category, attrs))
            else:
                add_open("destination", stack_surface.group(1), stack_surface.start(1))

    # "目标是..." is the authoritative theme description.  Destination
    # nouns in the action clause must be captured before the target clause is
    # considered, otherwise the whole sentence is treated as a theme-only
    # mention and the action cannot be dispatched.
    if "destination" not in role_refs and (set(actions) & {"PLACE", "TRANSFER", "FETCH", "STACK", "POUR"}):
        surface_destination = re.search(
            r"(?:\u4ea4\u7ed9|\u79fb\u4ea4\u7ed9|\u8f6c\u4ea4\u7ed9|\u5b89\u7f6e\u6210|\u6210\u4e3a|\u653e\u56de|\u653e\u5230|\u653e\u5165|\u9001\u56de|\u53d6\u5230)\s*([^，。；,;]+?)(?=，|。|；|,|;|$)",
            text,
        )
        if surface_destination:
            hit = _find_mention(surface_destination.group(1), after=0)
            if hit:
                mention, local_start, category, attrs = hit
                if category not in {"human", "operator"}:
                    add("destination", (mention, surface_destination.start(1) + local_start, category, attrs))
            else:
                add_open("destination", surface_destination.group(1), surface_destination.start(1))

    if "STACK" in actions and "destination" not in role_refs:
        stack_surface = re.search(
            r"(?:\u6210\u4e3a|\u5b89\u7f6e\u6210|\u653e\u6210|\u6446\u6210)\s*([^，。；,;]+?)(?:\u7684)?(?:\u4e0a\u5c42\u7269\u4f53|\u4e0a\u9762\u7684\u90a3\u4e00\u4ef6|\u9876\u5c42)",
            text,
        )
        if stack_surface:
            hit = _find_mention(stack_surface.group(1), after=0)
            if hit:
                mention, local_start, category, attrs = hit
                add("destination", (mention, stack_surface.start(1) + local_start, category, attrs))
            else:
                add_open("destination", stack_surface.group(1), stack_surface.start(1))

    # Waiting clauses have two different semantic roles: the object whose
    # state is observed and the object manipulated after “再/然后”.
    wait_match = re.search(
        r"(?:等|等待|直到)\s*(.+?)\s*(?:停止|稳定|完成).*?(?:再|然后|之后)\s*"
        r"(?:抓|拿|取|夹住|抓取)\s*(.+?)(?:[，。；,;]|$)", text
    )
    if wait_match:
        add("condition", _find_mention(wait_match.group(1), after=0))
        add("theme", _find_mention(wait_match.group(2), after=0))

    # ``让夹具把红色杯子控住`` and similar agent/tool constructions put the
    # manipulated object directly after 把/将. Bind that direct object before
    # the broad first-noun fallback, so a fixture or gripper is not promoted
    # to the theme merely because it appears earlier in the sentence.
    if "theme" not in role_refs:
        direct_theme = re.search(
            r"(?:把|将|请将)\s*(?P<theme>[^，。；,;]+?)\s*"
            r"(?=(?:送到|送至|运到|移到|搬到|转移到|转运到|放到|放在|放入|"
            r"拿起|拿住|抓住|抓取|夹住|控住|提起|取回|带回|带到|纳入|顶开|滑过|滑过去|滑动到|递给|交给|推开|倾倒|倾空|"
            r"叠合|叠放|摞到|堆到|$))",
            text,
        )
        if direct_theme:
            # ``不要把旁边的同类物体混进去`` is a prohibition clause, not
            # the manipulated object.  Do not let the generic ``把`` pattern
            # claim it before the positive handover clause is parsed.
            command_prefix = text[max(0, direct_theme.start() - 6):direct_theme.start()]
            if re.search(r"(?:不要|别|禁止|避免)\s*$", command_prefix):
                direct_theme = None
        if direct_theme:
            # Search the original instruction at the captured span.  Running
            # the noun lookup on only the captured suffix loses descriptors
            # that occur before ``把`` (for example the scene-position prefix
            # used by the open-language acceptance set).
            add("theme", _find_mention(text, after=direct_theme.start("theme")))

    # Some handover clauses put the manipulated object after the delivery
    # verb: ``向操作员交付绿色盒子``.  The recipient parser above owns
    # ``操作员``; this pattern owns only the delivered object and leaves the
    # final scene ID to deterministic grounding.
    if "HANDOVER" in actions and "theme" not in role_refs:
        handover_theme = re.search(
            r"(?:交付|递交|转交)\s*(?P<theme>[^，。；,;]+?)(?=[，。；,;]|$)",
            text,
        )
        if handover_theme:
            add("theme", _find_mention(text, after=handover_theme.start("theme")))

    surface_theme_match = re.search(
        r"(?:把|将|请将)\s*(?:桌上|桌面上|台上|托盘上|工位上)的"
        r"(?P<theme>[^，。；,;]+?)(?=(?:递|交|送|放|拿|抓|取|提))", text
    )
    if surface_theme_match:
        add("theme", _find_mention(surface_theme_match.group("theme"), after=0))

    # Industrial templates fill role hints without making recipient mandatory.
    for template in match_industrial_templates(text):
        for role, surface in template.role_hints.items():
            # A template role is only evidence when its surface is actually
            # present in the instruction.  Passing ``surface`` as the search
            # text would manufacture entities such as 周转箱 for “放到托盘”.
            if surface in text:
                hit = _find_mention(text, after=text.find(surface))
                if hit and surface not in hit[0]:
                    hit = None
                add(role, hit)

    # Negated object is not the main theme; let the negation parser own it.
    positive_text = re.sub(
        r"(?:在)?(?:不接触|不要接触|不碰|不要碰到|避免接触|without touching|without contact)"
        r"[^，。；,;]*?(?:的情况下|情况下)", "", text, flags=re.IGNORECASE,
    )
    positive_text = re.sub(r"(?:别|不要|禁止|避免)[^，。；,;]+[，。；,;]?", "", positive_text)
    # Route obstacles are not the manipulated theme. Remove only the
    # terminated clause so a following delivery clause supplies the theme.
    positive_text = re.sub(
        r"(?:\u7ed5\u8fc7|\u7ed5\u5f00|\u8eb2\u5f00|\u907f\u5f00)[^，。；,;]*[，,;]\s*",
        "", positive_text,
    )
    avoid_action = re.search(
        r"(?:避开|绕开|不要碰到|不要碰|别碰|avoid|don't touch|do not touch)"
        r"\s*(?P<obstacle>.+?)(?=(?:抓取|抓住|抓稳|拿起|取起|pick|grasp|grab))"
        r"(?:抓取|抓住|抓稳|拿起|取起|pick|grasp|grab)\s*(?P<theme>[^，。；,;]+)",
        text, re.IGNORECASE,
    )
    if avoid_action:
        add("theme", _find_mention(avoid_action.group("theme"), after=0))
    theme_hit = _find_mention(positive_text, after=0)
    if wait_match and "theme" in role_refs:
        theme_hit = None
    if theme_hit and theme_hit[2] in {"human", "operator"}:
        object_hits = []
        for category in sorted(set(CATEGORIES.values()) - {"human", "operator", "table", "tray", "workbench"}):
            hit = _find_mention(positive_text, category=category, after=0)
            if hit:
                object_hits.append(hit)
        if object_hits:
            theme_hit = min(object_hits, key=lambda item: item[1])
    has_explicit_theme_verb = bool(re.search(r"(?:把|将|请将|抓|拿|取|放|移|搬|送|运|转|递|交)", positive_text))
    if "theme" in role_refs and surface_theme_match:
        theme_hit = None
    if theme_hit and ("obstacle" not in role_refs or has_explicit_theme_verb):
        original_start = text.find(theme_hit[0])
        if original_start >= 0:
            theme_hit = (theme_hit[0], original_start, theme_hit[2], theme_hit[3])
        if "theme" not in role_refs:
            add("theme", theme_hit)
    elif (actions and "obstacle" not in role_refs and "condition" not in role_refs
          and not surface_theme_match):
        add("theme", _find_mention(text, after=0))

    obstacle_match = re.search(
        r"(?:避开|绕开|不要碰到|不要碰|别碰|不碰|不想碰|avoid|don't touch|do not touch|not touch)"
        r"\s*([^，。；,;]+?)(?=(?:抓取|抓住|抓稳|拿起|取起|pick|grasp|grab)|[，。；,;]|$)",
        text, re.IGNORECASE,
    )
    if obstacle_match:
        obstacle_text = obstacle_match.group(1).strip()
        hit = _find_mention(obstacle_text, after=0)
        add("obstacle", hit)
        if "obstacle" not in role_refs:
            add_open("obstacle", obstacle_text, obstacle_match.start(1))
    if "obstacle" not in role_refs:
        obstacle_match = re.search(r"(?:绕过|躲开|避开)([^，。；,;]+)", text)
        if obstacle_match:
            add("obstacle", _find_mention(obstacle_match.group(1), after=0))
    if "obstacle" not in role_refs:
        # Compact prohibition form: "不要玻璃杯". Numeric bounds and action
        # prohibitions remain owned by their dedicated parsers.
        bare_obstacle = re.search(
            r"(?:\u4e0d\u8981|\u522b)(?!\s*(?:\u8d85\u8fc7|\u5927\u4e8e|\u5c0f\u4e8e|\u4f4e\u4e8e|\u9ad8\u4e8e|\u4f7f\u7528|\u7528|\u62ff|\u53d6|\u6293|\u653e|\u79fb|\u8fd0))"
            r"\s*([^，。；,;]+)",
            text,
        )
        if bare_obstacle:
            hit = _find_mention(bare_obstacle.group(1), after=0)
            if hit:
                add("obstacle", hit)
    if "obstacle" not in role_refs:
        path_obstacle = re.search(
            r"(?:路径|路线)\s*(?:别|不要|不|避免)\s*(?:经过|通过|碰到|接触)\s*"
            r"([^，。；,;]+)", text
        )
        if path_obstacle:
            obstacle_text = path_obstacle.group(1).strip()
            hit = _find_mention(obstacle_text, after=0)
            if hit:
                # _find_mention is run on the captured suffix, so its offset
                # is local to that suffix. Rebase it before attribute
                # enrichment; otherwise the preceding target clause can leak
                # color/material into the obstacle entity.
                mention, _, category, attrs = hit
                actual_start = text.find(mention, path_obstacle.start(1))
                if actual_start >= 0:
                    hit = (mention, actual_start, category, attrs)
            add("obstacle", hit)
            if "obstacle" not in role_refs:
                add_open("obstacle", obstacle_text, path_obstacle.start(1))
    # Scoped avoidance: "在不接触 X 的情况下 ..." must create an obstacle
    # role before positive-theme extraction can discard the clause.
    if "obstacle" not in role_refs:
        scoped_obstacle = re.search(
            r"(?:不接触|不要接触|不碰|不要碰到|避免接触|without touching|without contact)"
            r"\s*([^，。；,;]+?)(?=的情况下|情况下|时|再|然后|拿起|抓取|抓稳|$)",
            text, re.IGNORECASE,
        )
        if scoped_obstacle:
            obstacle_text = scoped_obstacle.group(1).strip()
            add("obstacle", _find_mention(obstacle_text, after=0))
            if "obstacle" not in role_refs:
                add_open("obstacle", obstacle_text, scoped_obstacle.start(1))
    if "obstacle" not in role_refs:
        spatial_obstacle = re.search(r"(?:不要|别|不想|avoid|don't|do not)\s*(前面的|后面的|旁边的|front|behind|nearby)", text, re.IGNORECASE)
        if spatial_obstacle:
            cue = spatial_obstacle.group(1).lower()
            relation = "BEHIND" if ("后" in cue or cue == "behind") else ("FRONT" if ("前" in cue or cue == "front") else "NEAR")
            add_open("obstacle", spatial_obstacle.group(1), spatial_obstacle.start(1), {"spatial_relation": relation})
    if "obstacle" not in role_refs:
        universal = re.search(r"(?:anything else|everything else|其他任何东西|周围的东西)", text, re.IGNORECASE)
        if universal:
            add_open("obstacle", universal.group(0), universal.start())
    if "obstacle" not in role_refs:
        # Compound command: “抓住A，别碰B”.  The second clause is a
        # prohibition role even when the obstacle verb is not immediately
        # adjacent to the noun in the compact pattern above.
        compound_obstacle = re.search(
            r"(?:抓住|抓取|拿起|拿|取|夹住)\s*[^，。；,;]+[，,]\s*"
            r"(?:别碰|不要碰|不碰|禁止接触|不要接触)\s*(.+?)(?:[，。；,;]|$)", text
        )
        if compound_obstacle:
            obstacle_text = compound_obstacle.group(1).strip()
            hit = _find_mention(obstacle_text, after=0)
            if hit:
                add("obstacle", hit)
            else:
                add_open("obstacle", obstacle_text, compound_obstacle.start(1))
    if "obstacle" not in role_refs:
        color_only = re.search(r"(?:别|不要|禁止|避免)(?:拿|抓|取|碰|接触)?\s*(红色|蓝色|绿色|黄色|白色|黑色|透明)(?:的)?", text)
        if color_only:
            color = COLORS.get(color_only.group(1))
            mention = color_only.group(1) + "的"
            add("obstacle", (mention, color_only.start(1), "object", {"color": color}))

    # A vague peer reference such as ``不要把旁边的同类物体混进来`` does not
    # identify a physical entity. Do not fabricate an obstacle role that will
    # later make an otherwise complete HANDOVER/PUSH task unexecutable.
    if vague_peer_avoidance:
        vague_ref = role_refs.pop("obstacle", None)
        if vague_ref and not any(ref == vague_ref for ref in role_refs.values()):
            entities = [item for item in entities if item.local_ref != vague_ref]

    # Open industrial names are still valid mentions even when the compact
    # lexicon has no category alias (e.g. “镜片盒”, “电源箱”).
    if "theme" not in role_refs:
        open_theme = re.search(r"(?:把|将|请将|快把)\s*([^，。；,;]+?)\s*(?:拿过来|取过来|抓过来|拿起|抓取|放到|放入|移到)", text)
        if open_theme:
            mention = open_theme.group(1).strip()
            local_ref = f"entity-{len(entities) + 1}"
            entities.append(_entity(local_ref, mention, "object", text,
                                     open_theme.start(1), {}, None))
            role_refs["theme"] = local_ref

    # Attribute-before-noun forms require a second pass after role markers;
    # _find_mention may start at the noun and otherwise lose “颜色为红色”.
    for local_ref in list(role_refs.values()):
        entity = next((item for item in entities if item.local_ref == local_ref), None)
        if entity is None or not entity.mention:
            continue
        hit = _apply_attribute_only_mention(
            (entity.mention, text.find(entity.mention), entity.category, entity.attributes), text
        )
        if hit:
            entity.attributes.update(hit[3])

    # Convert common Chinese size/shape descriptors into the contract's
    # normalized attributes.  Grounding remains responsible for choosing the
    # scene object; these descriptors only narrow the candidate set.
    for entity in entities:
        mention = entity.mention or ""
        if any(token in mention for token in ("偏小", "小型", "较小")) or re.search(r"(?<!大)小的", mention):
            entity.attributes.setdefault("size", "small")
        elif any(token in mention for token in ("偏大", "较大", "大型")) or re.search(r"(?<!大)大的", mention):
            entity.attributes.setdefault("size", "large")
        elif any(token in mention for token in ("中等", "适中")):
            entity.attributes.setdefault("size", "medium")
        if any(token in mention for token in ("细长", "长条")):
            entity.attributes.setdefault("shape", "elongated")
        elif any(token in mention for token in ("矮胖", "短粗")):
            entity.attributes.setdefault("shape", "compact")

    # PLACE destination is also a support surface role at the semantic level.
    if "destination" in role_refs and ("PLACE" in actions or "TRANSFER" in actions):
        role_refs["support_surface"] = role_refs["destination"]
    return entities, role_refs
