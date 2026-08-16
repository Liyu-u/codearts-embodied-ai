"""Deterministic action contracts for the supported robot domain."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional


@dataclass(frozen=True)
class ActionSchema:
    """Contract describing roles, effects and executable skill template."""

    action: str
    required_roles: tuple[str, ...] = ()
    required_any_roles: tuple[tuple[str, ...], ...] = ()
    optional_roles: tuple[str, ...] = ()
    forbidden_roles: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    skill_template: tuple[str, ...] = ()
    forbidden_conditions: tuple[str, ...] = ()
    role_required_affordances: tuple[tuple[str, tuple[str, ...]], ...] = ()
    symbolic_roles: tuple[str, ...] = ()
    missing_role_policy: str = "NEEDS_CLARIFICATION"
    destination_role_types: tuple[str, ...] = ()

    def accepts_role(self, role: str) -> bool:
        return role in self.required_roles or role in self.optional_roles

    def missing_roles(self, roles: Iterable[str]) -> List[str]:
        present = set(roles)
        missing = [role for role in self.required_roles if role not in present]
        for alternatives in self.required_any_roles:
            if not any(role in present for role in alternatives):
                missing.append("_or_".join(alternatives))
        return missing

    def has_forbidden_role(self, role: str) -> bool:
        return role in self.forbidden_roles

    def required_affordances_for(self, role: str) -> tuple[str, ...]:
        for name, affordances in self.role_required_affordances:
            if name == role:
                return affordances
        return ()

    def model_dump(self) -> Dict[str, object]:
        return {
            "action": self.action,
            "required_roles": list(self.required_roles),
            "required_any_roles": [list(group) for group in self.required_any_roles],
            "optional_roles": list(self.optional_roles),
            "forbidden_roles": list(self.forbidden_roles),
            "preconditions": list(self.preconditions),
            "effects": list(self.effects),
            "skill_template": list(self.skill_template),
            "forbidden_conditions": list(self.forbidden_conditions),
            "role_required_affordances": {
                role: list(values) for role, values in self.role_required_affordances
            },
            "symbolic_roles": list(self.symbolic_roles),
            "missing_role_policy": self.missing_role_policy,
            "destination_role_types": list(self.destination_role_types),
        }


def _schema(
    action: str,
    required: tuple[str, ...],
    optional: tuple[str, ...],
    forbidden: tuple[str, ...],
    preconditions: tuple[str, ...],
    effects: tuple[str, ...],
    skills: tuple[str, ...],
    forbidden_conditions: tuple[str, ...] = (),
    required_any_roles: tuple[tuple[str, ...], ...] = (),
    role_required_affordances: tuple[tuple[str, tuple[str, ...]], ...] = (),
    symbolic_roles: tuple[str, ...] = (),
    missing_role_policy: str = "NEEDS_CLARIFICATION",
    destination_role_types: tuple[str, ...] = (),
) -> ActionSchema:
    return ActionSchema(
        action=action,
        required_roles=required,
        required_any_roles=required_any_roles,
        optional_roles=optional,
        forbidden_roles=forbidden,
        preconditions=preconditions,
        effects=effects,
        skill_template=skills,
        forbidden_conditions=forbidden_conditions,
        role_required_affordances=role_required_affordances,
        symbolic_roles=symbolic_roles,
        missing_role_policy=missing_role_policy,
        destination_role_types=destination_role_types,
    )


ACTION_SCHEMAS: Dict[str, ActionSchema] = {
    "GRASP": _schema(
        "GRASP", ("theme",), ("obstacle",), ("destination", "recipient"),
        ("theme_grounded", "theme_graspable"), ("theme_in_hand",),
        ("Reach", "Grasp"),
        role_required_affordances=(("theme", ("graspable", "movable")),),
    ),
    "FETCH": _schema(
        "FETCH", ("theme",), ("destination", "recipient", "source", "obstacle"), (),
        ("theme_grounded", "destination_grounded"), ("theme_in_hand", "theme_at_destination"),
        ("Reach", "Grasp", "Fetch"), required_any_roles=(("destination", "recipient"),),
        role_required_affordances=(("theme", ("graspable", "movable")), ("destination", ("fixed", "container"))),
        symbolic_roles=("recipient",), destination_role_types=("receive_zone", "fixed", "container"),
    ),
    "PLACE": _schema(
        "PLACE", ("theme", "destination"), ("source", "obstacle"), ("recipient",),
        ("theme_grounded", "destination_grounded", "destination_supports_placement"),
        ("theme_at_destination",), ("Reach", "Grasp", "Transport", "Place"),
        role_required_affordances=(("theme", ("graspable", "movable")), ("destination", ("support_surface", "fixed", "container"))),
        destination_role_types=("support_surface", "fixed", "container"),
    ),
    "TRANSFER": _schema(
        "TRANSFER", ("theme", "destination"), ("source", "obstacle"), ("recipient",),
        ("theme_grounded", "destination_grounded"), ("theme_at_destination",),
        ("Reach", "Grasp", "Transport", "Place"),
        role_required_affordances=(("theme", ("graspable", "movable")),),
        destination_role_types=("receive_zone", "fixed", "container"),
    ),
    "HANDOVER": _schema(
        "HANDOVER", ("theme", "recipient"), ("destination", "source", "obstacle"), (),
        ("theme_grounded", "recipient_grounded", "handover_pose_known"),
        ("recipient_has_theme",), ("Reach", "Grasp", "MoveToHandoverZone", "Handover"),
        role_required_affordances=(("theme", ("graspable", "movable")),),
        symbolic_roles=("recipient",),
    ),
    "DYNAMIC_GRASP": _schema(
        "DYNAMIC_GRASP", ("theme",), ("obstacle",), ("destination", "recipient"),
        ("theme_grounded", "tracking_available"), ("theme_in_hand",),
        ("WaitUntilStable", "Reach", "DynamicGrasp"),
        role_required_affordances=(("theme", ("graspable", "movable", "trackable")),),
    ),
    "WAIT": _schema(
        "WAIT", ("condition",), (), (),
        ("condition_observable",), ("condition_satisfied",), ("WaitUntil",),
    ),
    "PUSH": _schema(
        "PUSH", ("theme",), ("destination", "obstacle"), (),
        ("theme_grounded", "theme_pushable", "push_path_clear"),
        ("theme_moved",), ("Reach", "Push", "Release"),
        role_required_affordances=(("theme", ("pushable", "movable")),),
    ),
    "STACK": _schema(
        "STACK", ("theme", "destination"), ("obstacle",), (),
        ("theme_grounded", "destination_stable"),
        ("theme_on_destination",), ("Reach", "Grasp", "MoveTo", "Stack", "Release"),
        role_required_affordances=(("theme", ("graspable", "movable")), ("destination", ("support_surface", "fixed", "container"))),
        destination_role_types=("support_surface", "fixed", "container"),
    ),
    "POUR": _schema(
        "POUR", ("theme", "destination"), ("obstacle",), (),
        ("theme_grounded", "destination_container"),
        ("contents_transferred",), ("Reach", "Grasp", "MoveTo", "Pour", "Release"),
        role_required_affordances=(("theme", ("graspable", "pourable")), ("destination", ("container", "support_surface"))),
        destination_role_types=("container", "support_surface"),
    ),
    "CUSTOM": _schema(
        "CUSTOM", (), (), (), (), (), (),
    ),
}


_ALIASES = {
    "抓": "GRASP", "抓取": "GRASP", "拿起": "GRASP", "取起": "GRASP",
    "取": "GRASP", "放": "PLACE", "放入": "PLACE", "放到": "PLACE",
    "上料": "TRANSFER", "移到": "TRANSFER", "转移": "TRANSFER", "搬运": "TRANSFER",
    "递给": "HANDOVER", "交给": "HANDOVER", "传给": "HANDOVER",
    "等": "WAIT", "等待": "WAIT",
    "推": "PUSH", "挪": "PUSH",
    "摞": "STACK", "叠": "STACK", "堆": "STACK",
    "倒": "POUR", "倾倒": "POUR",
}


def normalize_action(value: Optional[str]) -> str:
    if not value:
        return "CUSTOM"
    upper = str(value).strip().upper()
    if upper in ACTION_SCHEMAS:
        return upper
    return _ALIASES.get(str(value).strip(), "CUSTOM")


def get_action_schema(action: Optional[str]) -> ActionSchema:
    return ACTION_SCHEMAS.get(normalize_action(action), ACTION_SCHEMAS["CUSTOM"])
