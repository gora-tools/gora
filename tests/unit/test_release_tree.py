"""The rules between the release files of one commit (ADR-026 sections 2, 3, 5, 9 and 14).

Built from the four examples as the entries of one commit; each case changes the least that shows
its rule, and writes out the file, the path and the rule of every problem. No message holds a
value from a document: grammar-valid sentinels make every rule fire.
"""

import re
import types
from typing import Any

import pytest

from gora.domain.release_repository import files, v1alpha1
from gora.domain.release_repository.formats import REASON
from gora.domain.release_repository.inputs import fixed_components, is_release_path
from gora.domain.release_repository.tree import (
    RULE_AUTO_APPROVERS,
    RULE_BOTH_NAMES,
    RULE_EMPTY_PREFIX,
    RULE_NO_APPROVERS,
    RULE_NO_BUILDER,
    RULE_NO_REPORTER,
    RULE_NOT_A_STAGE,
    RULE_RELEASE_INPUT,
    InvalidReleaseTreeError,
    ReleaseTree,
    TreeEntry,
    is_layout_path,
    load_tree,
)
from tests.unit.test_release_models import DELETE, EXAMPLES, PATHS, edited, example, text

RELEASE_REPOSITORY = "github.com/acme/cally-release"
POLICY = example("policy")
ARTIFACT = example("artifact")
STAGE_STATE = example("stage-state")
REJECTION = example("rejection")
POLICY_PATH = PATHS["policy"]
ARTIFACT_PATH = PATHS["artifact"]
STATE_PATH = PATHS["stage-state"]
REJECTION_PATH = PATHS["rejection"]
DATA = {PATHS[name]: (EXAMPLES / f"{name}.yaml").read_bytes() for name in PATHS}
EXAMPLE_ENTRIES = tuple(TreeEntry(path.encode(), "100644", DATA[path]) for path in sorted(DATA))
STAGES = ("spec", "stages")

Found = list[tuple[str, str, str]]


def entry(path: str, document: object) -> TreeEntry:
    return TreeEntry(path.encode(), "100644", text(document))


def with_entries(*added: TreeEntry, removed: tuple[str, ...] = ()) -> list[TreeEntry]:
    """The examples, with ``added`` in place of the entry at the same path or beside them, and
    the entries at ``removed`` taken out."""
    entries = {item.path: item for item in EXAMPLE_ENTRIES}
    for path in removed:
        del entries[path.encode()]
    entries.update({item.path: item for item in added})
    return list(entries.values())


def declaration(name: str) -> dict[str, Any]:
    """The example declaration as another artifact: its name, and the tag prefix of a later one."""
    document = edited(ARTIFACT, ("metadata", "name"), name)
    return edited(document, ("spec", "source", "tagPrefix"), f"{name}/")


def state(stage: str, artifact: str) -> TreeEntry:
    document = edited(STAGE_STATE, ("metadata", "stage"), stage)
    document = edited(document, ("metadata", "artifact"), artifact)
    return entry(f"state/{stage}/{artifact}.yaml", document)


def rejection(artifact: str, version: str = "1.1.0") -> TreeEntry:
    document = edited(REJECTION, ("metadata", "artifact"), artifact)
    document = edited(document, ("metadata", "version"), version)
    return entry(f"rejections/{artifact}/{version}.yaml", document)


def load(entries: list[TreeEntry], repository: str = RELEASE_REPOSITORY) -> ReleaseTree:
    return load_tree(entries, release_repository=repository)


def refused(entries: list[TreeEntry], repository: str = RELEASE_REPOSITORY) -> Found:
    with pytest.raises(InvalidReleaseTreeError) as raised:
        load_tree(entries, release_repository=repository)
    return [(problem.source, problem.path, problem.rule) for problem in raised.value.problems]


# --- the examples and what the tree gives ---------------------------------------------------------


def test_the_examples_load_into_a_tree() -> None:
    tree = load(with_entries())
    assert tree.policy.location == files.Location("Policy")
    assert isinstance(tree.policy.model, v1alpha1.Policy)
    assert list(tree.artifacts) == ["api"]
    assert list(tree.states) == [("qa", "api")]
    assert list(tree.rejections) == [("api", "1.1.0")]
    assert tree.retiring_artifacts() == frozenset()
    assert tree.removed_stages() == frozenset()


def test_the_tree_s_mappings_are_read_only() -> None:
    tree = load(with_entries())
    for mapping in (tree.artifacts, tree.states, tree.rejections):
        assert isinstance(mapping, types.MappingProxyType)
        writable: Any = mapping
        with pytest.raises(TypeError):
            writable["x"] = tree.policy


def test_the_tree_s_mappings_are_in_path_order() -> None:
    entries = [
        state("qa", "web"),
        state("dev", "api"),
        rejection("api", "1.9.0"),
        rejection("api", "1.10.0"),
        entry("artifacts/web/artifact.yaml", declaration("web")),
    ]
    tree = load(with_entries(*entries))
    assert list(tree.artifacts) == ["api", "web"]
    assert list(tree.states) == [("dev", "api"), ("qa", "api"), ("qa", "web")]
    # The path's order, not the versions': "1.10.0" is before "1.9.0" as bytes.
    assert list(tree.rejections) == [("api", "1.1.0"), ("api", "1.10.0"), ("api", "1.9.0")]


def test_stage_for_adds_the_declaration_to_the_policy_s_stage() -> None:
    effective = load(with_entries()).stage_for("qa", "api")
    assert (effective.name, effective.gate) == ("qa", "approval")
    assert list(effective.approvers.items()) == [("team/qa", 1), ("team/api-qa", 1)]
    assert effective.verify == ("deploy", "e2e", "contract")
    assert isinstance(effective.approvers, types.MappingProxyType)


def test_stage_for_takes_the_higher_number_for_a_group_both_name() -> None:
    higher = {"team/api-qa": 1, "team/qa": 3}
    document = edited(ARTIFACT, (*STAGES, "qa", "approvers"), higher)
    effective = load(with_entries(entry(ARTIFACT_PATH, document))).stage_for("qa", "api")
    assert list(effective.approvers.items()) == [("team/qa", 3), ("team/api-qa", 1)]
    policy = edited(POLICY, (*STAGES, 1, "approvers"), {"team/qa": 2})
    document = edited(ARTIFACT, (*STAGES, "qa", "approvers"), {"team/qa": 1, "team/api-qa": 1})
    entries = with_entries(entry(POLICY_PATH, policy), entry(ARTIFACT_PATH, document))
    effective = load(entries).stage_for("qa", "api")
    assert list(effective.approvers.items()) == [("team/qa", 2), ("team/api-qa", 1)]


def test_stage_for_names_a_verification_both_name_once_in_the_policy_s_place() -> None:
    document = edited(ARTIFACT, (*STAGES, "qa", "verify"), ["contract", "e2e"])
    effective = load(with_entries(entry(ARTIFACT_PATH, document))).stage_for("qa", "api")
    assert effective.verify == ("deploy", "e2e", "contract")


def test_stage_for_an_auto_stage_gives_no_approvers() -> None:
    document = edited(ARTIFACT, (*STAGES, "dev"), {"verify": ["contract"]})
    effective = load(with_entries(entry(ARTIFACT_PATH, document))).stage_for("dev", "api")
    assert (effective.gate, dict(effective.approvers)) == ("auto", {})
    assert effective.verify == ("deploy", "integration", "contract")


def test_stage_for_a_stage_the_declaration_does_not_name_is_the_policy_s() -> None:
    effective = load(with_entries()).stage_for("staging", "api")
    assert list(effective.approvers.items()) == [("team/qa", 1)]
    assert effective.verify == ("deploy", "smoke")


@pytest.mark.parametrize(
    ("stage", "artifact"),
    [("uat", "api"), ("qa", "web"), ("api", "qa")],
    ids=["removed-stage", "retiring-artifact", "swapped"],
)
def test_stage_for_refuses_a_stage_not_in_the_policy_or_an_artifact_with_no_declaration(
    stage: str, artifact: str
) -> None:
    tree = load(with_entries(state("uat", "api"), state("qa", "web")))
    with pytest.raises(KeyError):
        tree.stage_for(stage, artifact)


# --- rule 1: one namespace (section 3) ------------------------------------------------------------


def test_rule_1_a_declaration_named_as_a_stage() -> None:
    added = entry("artifacts/dev/artifact.yaml", declaration("dev"))
    assert refused(with_entries(added)) == [
        ("artifacts/dev/artifact.yaml", "metadata.name", RULE_BOTH_NAMES)
    ]


def test_rule_1_control_a_declaration_with_a_name_of_its_own() -> None:
    tree = load(with_entries(entry("artifacts/web/artifact.yaml", declaration("web"))))
    assert list(tree.artifacts) == ["api", "web"]


def test_rule_1_a_state_file_s_artifact_named_as_a_stage_of_the_policy() -> None:
    assert refused(with_entries(state("qa", "dev"))) == [
        ("state/qa/dev.yaml", "metadata.artifact", RULE_BOTH_NAMES)
    ]


def test_rule_1_state_files_on_the_stage_side_and_the_artifact_side() -> None:
    # web is a stage by the directory of state/web/, and an artifact by state/qa/web.yaml.
    assert refused(with_entries(state("qa", "web"), state("web", "ui"))) == [
        ("state/qa/web.yaml", "metadata.artifact", RULE_BOTH_NAMES),
        ("state/web/ui.yaml", "metadata.stage", RULE_BOTH_NAMES),
    ]


def test_rule_1_a_state_file_s_stage_named_as_a_declared_artifact() -> None:
    assert refused(with_entries(state("api", "web"))) == [
        (ARTIFACT_PATH, "metadata.name", RULE_BOTH_NAMES),
        (REJECTION_PATH, "metadata.artifact", RULE_BOTH_NAMES),
        ("state/api/web.yaml", "metadata.stage", RULE_BOTH_NAMES),
        (STATE_PATH, "metadata.artifact", RULE_BOTH_NAMES),
    ]


def test_rule_1_a_rejection_s_artifact_named_as_a_stage() -> None:
    assert refused(with_entries(rejection("dev", "1.0.0"))) == [
        ("rejections/dev/1.0.0.yaml", "metadata.artifact", RULE_BOTH_NAMES)
    ]


def test_rule_1_counts_the_name_of_a_located_path_whose_file_is_refused() -> None:
    executable = TreeEntry(b"artifacts/dev/artifact.yaml", "100755", text(declaration("dev")))
    assert refused(with_entries(executable, state("dev", "api"))) == [
        ("artifacts/dev/artifact.yaml", "$", "an executable file is not a release file"),
        ("state/dev/api.yaml", "metadata.stage", RULE_BOTH_NAMES),
    ]


# --- rules 2 to 5: what a declaration adds (sections 4 and 5) -------------------------------------


def test_rule_2_a_declaration_names_only_the_policy_s_builders() -> None:
    document = edited(ARTIFACT, ("spec", "builders"), ["cally-ci", "other-ci"])
    assert refused(with_entries(entry(ARTIFACT_PATH, document))) == [
        (ARTIFACT_PATH, "spec.builders[1]", RULE_NO_BUILDER)
    ]


def test_rule_3_a_declaration_names_only_the_policy_s_stages() -> None:
    document = edited(ARTIFACT, (*STAGES, "uat"), {"verify": ["contract"]})
    assert refused(with_entries(entry(ARTIFACT_PATH, document))) == [
        (ARTIFACT_PATH, "spec.stages.uat", RULE_NOT_A_STAGE)
    ]


def test_rule_3_a_declaration_names_approvers_only_on_an_approval_stage() -> None:
    document = edited(ARTIFACT, (*STAGES, "dev"), {"approvers": {"team/api-qa": 1}})
    assert refused(with_entries(entry(ARTIFACT_PATH, document))) == [
        (ARTIFACT_PATH, "spec.stages.dev.approvers", RULE_AUTO_APPROVERS)
    ]


def test_rule_3_control_verifications_on_an_auto_stage_and_approvers_on_an_approval_one() -> None:
    document = edited(ARTIFACT, (*STAGES, "dev"), {"verify": ["contract"]})
    document = edited(document, (*STAGES, "staging"), {"approvers": {"team/api-qa": 1}})
    tree = load(with_entries(entry(ARTIFACT_PATH, document)))
    assert list(tree.stage_for("staging", "api").approvers) == ["team/qa", "team/api-qa"]


def test_rule_4_every_verification_of_a_declaration_has_a_reporter() -> None:
    document = edited(ARTIFACT, (*STAGES, "qa", "verify"), ["contract", "unknown"])
    assert refused(with_entries(entry(ARTIFACT_PATH, document))) == [
        (ARTIFACT_PATH, "spec.stages.qa.verify[1]", RULE_NO_REPORTER)
    ]


def test_rule_4_control_a_reporter_no_stage_of_the_policy_names() -> None:
    assert all("contract" not in stage["verify"] for stage in POLICY["spec"]["stages"])
    assert "contract" in load(with_entries()).stage_for("qa", "api").verify


LEFT_TO_THE_DECLARATIONS = edited(POLICY, (*STAGES, 2, "approvers"), DELETE)
NAMES_STAGING_APPROVERS = edited(ARTIFACT, (*STAGES, "staging"), {"approvers": {"team/a": 1}})


def test_rule_5_every_declaration_names_approvers_for_a_stage_left_to_them() -> None:
    web = edited(declaration("web"), STAGES, DELETE)
    entries = with_entries(
        entry(POLICY_PATH, LEFT_TO_THE_DECLARATIONS),
        entry(ARTIFACT_PATH, NAMES_STAGING_APPROVERS),
        entry("artifacts/web/artifact.yaml", web),
    )
    assert refused(entries) == [
        (
            "artifacts/web/artifact.yaml",
            "spec.stages",
            "names no approvers for the stage at spec.stages[2] of the policy, which leaves them "
            "to the declarations",
        )
    ]


def test_rule_5_verifications_alone_name_no_approvers() -> None:
    document = edited(ARTIFACT, (*STAGES, "staging"), {"verify": ["contract"]})
    entries = with_entries(
        entry(POLICY_PATH, LEFT_TO_THE_DECLARATIONS), entry(ARTIFACT_PATH, document)
    )
    assert refused(entries) == [(ARTIFACT_PATH, "spec.stages", RULE_NO_APPROVERS.format(index=2))]


def test_rule_5_control_every_declaration_names_some() -> None:
    web = edited(declaration("web"), (*STAGES, "staging"), {"approvers": {"team/b": 2}})
    entries = with_entries(
        entry(POLICY_PATH, LEFT_TO_THE_DECLARATIONS),
        entry(ARTIFACT_PATH, NAMES_STAGING_APPROVERS),
        entry("artifacts/web/artifact.yaml", web),
    )
    tree = load(entries)
    assert dict(tree.stage_for("staging", "api").approvers) == {"team/a": 1}
    assert dict(tree.stage_for("staging", "web").approvers) == {"team/b": 2}


# --- rule 6: one empty tag prefix per source repository (section 5) -------------------------------


def test_rule_6_one_empty_tag_prefix_per_source_repository() -> None:
    web = edited(declaration("web"), ("spec", "source", "tagPrefix"), "")
    assert refused(with_entries(entry("artifacts/web/artifact.yaml", web))) == [
        ("artifacts/web/artifact.yaml", "spec.source.tagPrefix", RULE_EMPTY_PREFIX)
    ]


def test_rule_6_reports_in_the_declarations_after_the_first_in_path_order() -> None:
    abc = edited(declaration("abc"), ("spec", "source", "tagPrefix"), "")
    assert refused(with_entries(entry("artifacts/abc/artifact.yaml", abc))) == [
        (ARTIFACT_PATH, "spec.source.tagPrefix", RULE_EMPTY_PREFIX)
    ]


def test_rule_6_control_another_repository_has_its_own_empty_prefix() -> None:
    web = edited(declaration("web"), ("spec", "source", "tagPrefix"), "")
    web = edited(web, ("spec", "source", "repository"), "github.com/acme/cally-web")
    load(with_entries(entry("artifacts/web/artifact.yaml", web)))


# --- rule 7: no release file is an input (section 9) ----------------------------------------------


def declared_in(repository: str, glob: str) -> TreeEntry:
    document = edited(ARTIFACT, ("spec", "source", "repository"), repository)
    return entry(ARTIFACT_PATH, edited(document, ("spec", "source", "inputs"), ["src/**", glob]))


RELEASE_GLOBS = [
    "release-policy.yaml",
    ".github/CODEOWNERS",
    "artifacts/**",
    "state/**",
    "rejections/**",
    "state",
    "artifacts/api/artifact.yaml",
    "rejections/api/*.yaml",
]


@pytest.mark.parametrize("glob", RELEASE_GLOBS)
def test_rule_7_a_glob_naming_a_release_path_is_refused_in_the_release_repository(
    glob: str,
) -> None:
    assert refused(with_entries(declared_in(RELEASE_REPOSITORY, glob))) == [
        (ARTIFACT_PATH, "spec.source.inputs[1]", RULE_RELEASE_INPUT)
    ]


ACCEPTED_GLOBS = [
    ".github",
    ".github/**",
    "**",
    "*/x",
    "stat*",
    "s[t]ate/**",
    "states/**",
    "*.yaml",
]


@pytest.mark.parametrize("glob", ACCEPTED_GLOBS)
def test_rule_7_a_glob_whose_fixed_components_stop_before_a_release_path_is_accepted(
    glob: str,
) -> None:
    load(with_entries(declared_in(RELEASE_REPOSITORY, glob)))


@pytest.mark.parametrize(
    ("glob", "expected"),
    [(glob, True) for glob in RELEASE_GLOBS] + [(glob, False) for glob in ACCEPTED_GLOBS],
)
def test_rule_7_the_refusal_of_a_glob_and_the_removal_of_a_path_are_one_relation(
    glob: str, expected: bool
) -> None:
    """A glob is refused when its fixed components begin with a release path, and a path is
    removed from the input set when its components do (``inputs.is_release_path``): the path
    the fixed components of a glob spell is a release path exactly when the glob is refused."""
    spelled = "/".join(fixed_components(glob)).encode()
    assert is_release_path(spelled) is expected


@pytest.mark.parametrize("glob", RELEASE_GLOBS)
def test_rule_7_control_another_repository_s_paths_are_its_own(glob: str) -> None:
    load(with_entries(declared_in("github.com/acme/cally-api", glob)))


# --- rule 8: retiring and removed (section 14) ----------------------------------------------------


def test_rule_8_a_state_file_of_an_artifact_with_no_declaration_is_retiring() -> None:
    tree = load(with_entries(state("qa", "web")))
    assert tree.retiring_artifacts() == frozenset({"web"})
    assert list(tree.states) == [("qa", "api"), ("qa", "web")]


def test_rule_8_the_state_files_of_a_removed_declaration_are_retiring() -> None:
    tree = load(with_entries(removed=(ARTIFACT_PATH,)))
    assert tree.retiring_artifacts() == frozenset({"api"})
    assert dict(tree.artifacts) == {}


def test_rule_8_a_state_directory_the_policy_does_not_name_is_a_removed_stage() -> None:
    tree = load(with_entries(state("uat", "api")))
    assert tree.removed_stages() == frozenset({"uat"})
    assert tree.retiring_artifacts() == frozenset()


def test_rule_8_a_rejection_of_an_artifact_with_no_declaration_is_valid() -> None:
    tree = load(with_entries(rejection("web", "1.0.0")))
    assert list(tree.rejections) == [("api", "1.1.0"), ("web", "1.0.0")]
    assert tree.retiring_artifacts() == frozenset()


# --- 2.1: each entry on its own -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "data", "rule"),
    [
        (
            "120000",
            b"../../contracts/examples/v1alpha1/stage-state.yaml",
            "a symbolic link is not a release file",
        ),
        ("100755", DATA[STATE_PATH], "an executable file is not a release file"),
        ("160000", b"", "a gitlink is not a release file"),
    ],
    ids=["symbolic-link", "executable", "gitlink"],
)
def test_a_release_file_is_a_regular_file(mode: str, data: bytes, rule: str) -> None:
    found = refused(with_entries(TreeEntry(STATE_PATH.encode(), mode, data)))
    assert found == [(STATE_PATH, "$", rule)]


def test_a_symbolic_link_at_a_path_locate_refuses_gives_the_layout_problem_only() -> None:
    link = TreeEntry(b"state/qa/API.yaml", "120000", b"api.yaml")
    assert refused(with_entries(link)) == [("state/qa/API.yaml", "$", files.RULE_LAYOUT)]


def test_a_path_that_is_not_utf_8_is_refused_by_locate_and_shown_as_a_surrogate() -> None:
    undecodable = TreeEntry(b"artifacts/\xff/artifact.yaml", "100644", DATA[ARTIFACT_PATH])
    assert refused(with_entries(undecodable)) == [
        (r"artifacts/\udcff/artifact.yaml", "$", files.RULE_LAYOUT)
    ]


def test_a_tree_has_a_policy() -> None:
    assert refused(with_entries(removed=(POLICY_PATH,))) == [
        ("release-policy.yaml", "$", "required: a release repository has one policy")
    ]


def test_without_a_policy_the_rules_that_need_it_do_not_run() -> None:
    document = edited(ARTIFACT, ("spec", "builders"), ["other-ci"])
    entries = with_entries(entry(ARTIFACT_PATH, document), removed=(POLICY_PATH,))
    assert refused(entries) == [
        ("release-policy.yaml", "$", "required: a release repository has one policy")
    ]


def test_a_policy_present_but_refused_gives_only_its_own_problems() -> None:
    policy = edited(POLICY, ("spec", "branchAgeDays"), 0)
    assert refused(with_entries(entry(POLICY_PATH, policy))) == [
        (POLICY_PATH, "spec.branchAgeDays", "must be at least 1")
    ]
    executable = TreeEntry(POLICY_PATH.encode(), "100755", DATA[POLICY_PATH])
    assert refused(with_entries(executable)) == [
        (POLICY_PATH, "$", "an executable file is not a release file")
    ]


def test_with_the_policy_refused_rule_1_still_runs_on_the_names_of_the_paths() -> None:
    executable = TreeEntry(POLICY_PATH.encode(), "100755", DATA[POLICY_PATH])
    # qa is a stage by the directory of state/qa/, and an artifact by rejections/qa/; dev, a
    # stage only by the refused policy, is not counted.
    entries = with_entries(executable, rejection("qa"), state("qa", "dev"))
    assert refused(entries) == [
        ("rejections/qa/1.1.0.yaml", "metadata.artifact", RULE_BOTH_NAMES),
        (POLICY_PATH, "$", "an executable file is not a release file"),
        (STATE_PATH, "metadata.stage", RULE_BOTH_NAMES),
        ("state/qa/dev.yaml", "metadata.stage", RULE_BOTH_NAMES),
    ]


# --- the caller's errors --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wrong",
    [
        TreeEntry(b"README.md", "100644", b""),
        TreeEntry(b".github/CODEOWNERS", "100644", b""),
        TreeEntry(b"state", "100644", b""),
        TreeEntry(b"state/qa", "040000", b""),
        TreeEntry(b"state/qa/web.yaml", "040000", b""),
        TreeEntry(b"state/qa/web.yaml", "100664", b""),
    ],
    ids=["outside", "codeowners", "root-blob", "tree", "tree-at-a-file-s-path", "unknown-mode"],
)
def test_an_entry_the_caller_must_not_pass_is_a_value_error(wrong: TreeEntry) -> None:
    # Beside a tree with problems of its own (no policy): the caller's error comes first.
    with pytest.raises(ValueError) as raised:
        load(with_entries(wrong, removed=(POLICY_PATH,)))
    assert not isinstance(raised.value, InvalidReleaseTreeError)


def test_two_entries_with_one_path_are_a_value_error() -> None:
    with pytest.raises(ValueError) as raised:
        load([*with_entries(), TreeEntry(STATE_PATH.encode(), "120000", b"api.yaml")])
    assert not isinstance(raised.value, InvalidReleaseTreeError)


@pytest.mark.parametrize(
    "repository",
    [
        "cally-release",
        "github.com/acme/Cally-Release",
        "github.com/acme/cally-release.git",
        "https://github.com/acme/cally-release",
        "gitlab.com/acme/cally-release",
    ],
)
def test_a_release_repository_outside_its_grammar_is_a_value_error(repository: str) -> None:
    with pytest.raises(ValueError) as raised:
        load(with_entries(), repository)
    assert not isinstance(raised.value, InvalidReleaseTreeError)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (b"release-policy.yaml", True),
        (b"artifacts/api/artifact.yaml", True),
        (b"state/qa/api.yaml", True),
        (b"rejections/api/1.1.0.yaml", True),
        (b"artifacts/a", True),
        (b"state", False),
        (b"artifacts", False),
        (b"rejections", False),
        (b"artifactsx/a", False),
        (b".github/CODEOWNERS", False),
        (b"release-policy.yml", False),
        (b"State/qa/api.yaml", False),
        (b"Release-Policy.yaml", False),
    ],
)
def test_is_layout_path(path: bytes, expected: bool) -> None:
    assert is_layout_path(path) is expected


# --- 2.3: the order of problems -------------------------------------------------------------------


def test_problems_are_ordered_by_the_path_of_their_file_as_bytes() -> None:
    entries = [
        TreeEntry(STATE_PATH.encode(), "100755", DATA[STATE_PATH]),
        entry(REJECTION_PATH, edited(REJECTION, ("spec", "reason"), "")),
        TreeEntry(b"artifacts/\xff/artifact.yaml", "100644", b""),
        TreeEntry(b"artifacts/\xee\x80\x80/artifact.yaml", "100644", b""),
        TreeEntry(ARTIFACT_PATH.encode(), "120000", b"x"),
    ]
    assert refused(entries) == [
        (ARTIFACT_PATH, "$", "a symbolic link is not a release file"),
        # U+E000 is EE 80 80 in UTF-8, so before the byte FF; as text, \udcff would come first.
        (r"artifacts/\ue000/artifact.yaml", "$", files.RULE_LAYOUT),
        (r"artifacts/\udcff/artifact.yaml", "$", files.RULE_LAYOUT),
        (REJECTION_PATH, "spec.reason", REASON.rule),
        ("release-policy.yaml", "$", "required: a release repository has one policy"),
        (STATE_PATH, "$", "an executable file is not a release file"),
    ]


def test_problems_of_one_file_are_in_document_order() -> None:
    document = declaration("dev")
    spec = {name: value for name, value in document["spec"].items() if name != "stages"}
    # stages before builders in the file, while the code checks builders first.
    stages = {"uat": {"verify": ["contract"]}}
    document["spec"] = {"stages": stages, **spec, "builders": ["other-ci"]}
    assert refused(with_entries(entry("artifacts/dev/artifact.yaml", document))) == [
        ("artifacts/dev/artifact.yaml", "metadata.name", RULE_BOTH_NAMES),
        ("artifacts/dev/artifact.yaml", "spec.stages.uat", RULE_NOT_A_STAGE),
        ("artifacts/dev/artifact.yaml", "spec.builders[0]", RULE_NO_BUILDER),
    ]


# --- no value in a message ------------------------------------------------------------------------

SENTINEL = "zq9sentinel"
NAME = f"{SENTINEL}-name"
REPOSITORY = f"github.com/{SENTINEL}/{SENTINEL}-repo"
# The one name a case plants as a mapping key (rule-3-stage), the one place a document's text may
# be a step of a path: a key that reads as a field name is written there. A value that is shaped
# like a key (a builder, a verification) is not exempt, and no pattern stands in for the list.
PLANTED_KEYS = frozenset({f"{SENTINEL}-stage"})


def renamed(mapping: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    return {new if name == old else name: value for name, value in mapping.items()}


def sentinel_policy() -> dict[str, Any]:
    """The policy with staging renamed to a sentinel and left to the declarations."""
    document = edited(POLICY, (*STAGES, 2, "name"), f"{SENTINEL}-stage")
    document = edited(document, (*STAGES, 2, "approvers"), DELETE)
    for field in ("promote", "rollback"):
        entries = document["spec"]["requests"][field]
        document["spec"]["requests"][field] = renamed(entries, "staging", f"{SENTINEL}-stage")
    return document


def in_the_artifact(at: tuple[str, ...], value: object) -> list[TreeEntry]:
    return with_entries(entry(ARTIFACT_PATH, edited(ARTIFACT, at, value)))


SENTINEL_CASES: list[tuple[str, list[TreeEntry], str, str]] = [
    (
        "rule-1",
        with_entries(
            entry(f"artifacts/{NAME}/artifact.yaml", declaration(NAME)), state(NAME, "api")
        ),
        RELEASE_REPOSITORY,
        RULE_BOTH_NAMES,
    ),
    (
        "rule-2",
        in_the_artifact(("spec", "builders"), ["cally-ci", f"{SENTINEL}-builder"]),
        RELEASE_REPOSITORY,
        RULE_NO_BUILDER,
    ),
    (
        "rule-3-stage",
        in_the_artifact((*STAGES, f"{SENTINEL}-stage"), {"verify": ["contract"]}),
        RELEASE_REPOSITORY,
        RULE_NOT_A_STAGE,
    ),
    (
        "rule-3-approvers",
        in_the_artifact((*STAGES, "dev"), {"approvers": {f"team/{SENTINEL}-group": 1}}),
        RELEASE_REPOSITORY,
        RULE_AUTO_APPROVERS,
    ),
    (
        "rule-4",
        in_the_artifact((*STAGES, "qa", "verify"), [f"{SENTINEL}-verify"]),
        RELEASE_REPOSITORY,
        RULE_NO_REPORTER,
    ),
    (
        "rule-5",
        with_entries(entry(POLICY_PATH, sentinel_policy())),
        RELEASE_REPOSITORY,
        RULE_NO_APPROVERS.format(index=2),
    ),
    (
        "rule-6",
        with_entries(
            entry(ARTIFACT_PATH, edited(ARTIFACT, ("spec", "source", "repository"), REPOSITORY)),
            entry(
                f"artifacts/{NAME}/artifact.yaml",
                edited(
                    edited(declaration(NAME), ("spec", "source", "tagPrefix"), ""),
                    ("spec", "source", "repository"),
                    REPOSITORY,
                ),
            ),
        ),
        RELEASE_REPOSITORY,
        RULE_EMPTY_PREFIX,
    ),
    (
        "rule-7",
        with_entries(declared_in(REPOSITORY, f"state/{SENTINEL}-glob/**")),
        REPOSITORY,
        RULE_RELEASE_INPUT,
    ),
]


@pytest.mark.parametrize(
    ("entries", "repository", "rule"),
    [case[1:] for case in SENTINEL_CASES],
    ids=[case[0] for case in SENTINEL_CASES],
)
def test_no_value_reaches_a_message(entries: list[TreeEntry], repository: str, rule: str) -> None:
    with pytest.raises(InvalidReleaseTreeError) as raised:
        load_tree(entries, release_repository=repository)
    problems = raised.value.problems
    assert rule in {problem.rule for problem in problems}  # the rule fired
    sources = {item.path.decode() for item in entries}
    for problem in problems:
        assert SENTINEL not in problem.rule
        for step in re.split(r"[.\[\]]", problem.path):
            assert SENTINEL not in step or step in PLANTED_KEYS, problem.path
        assert problem.source in sources
