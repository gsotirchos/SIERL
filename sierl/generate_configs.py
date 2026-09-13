#!/usr/bin/env python3
"""Generate the config file of every experiment from the base templates.

The experiments are described in configs/experiments.yaml: each one is a
template plus the overrides of a "common", an environment and an algorithm
block, whose values may reference the parameters of that environment and
algorithm.
"""

import argparse
import copy
import re
import sys
from pathlib import Path

import yaml

PARAMETER_PATTERN = re.compile(r"\$\{([^}]+)\}")
DEFAULT_CONFIGS_DIR = Path(__file__).resolve().parent / "configs"
DEFAULT_PARAMETERS_FILE = DEFAULT_CONFIGS_DIR / "experiments.yaml"


class ParametersError(Exception):
    """Raised when the parameters file does not fit the templates."""


def aliased_paths(template_file):
    """Map every path of the template to the paths sharing its node, so that
    overriding a YAML anchor also overrides the aliases referring to it."""
    with open(template_file) as file:
        root = yaml.compose(file)

    paths_per_node = {}

    def walk(node, path):
        paths_per_node.setdefault(id(node), []).append(path)
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                walk(value, path + (key.value,))
        elif isinstance(node, yaml.SequenceNode):
            for index, value in enumerate(node.value):
                walk(value, path + (str(index),))

    walk(root, ())

    return {
        path: paths
        for paths in paths_per_node.values() if len(paths) > 1
        for path in paths
    }


def as_string(value):
    """Render a value the way the config names spell it out."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (list, tuple)):
        return "-".join(as_string(item) for item in value)
    return str(value)


def substitute(value, params):
    """Replace the "${...}" references of a parameter value."""
    if not isinstance(value, str):
        return value

    def lookup(match):
        try:
            return params[match.group(1)]
        except KeyError:
            raise ParametersError(f"Undefined parameter: {match.group(0)}") from None

    reference = PARAMETER_PATTERN.fullmatch(value)
    if reference:
        return lookup(reference)
    return PARAMETER_PATTERN.sub(lambda match: as_string(lookup(match)), value)


def item_at(config, path):
    item = config
    for segment in path:
        item = item[int(segment)] if isinstance(item, list) else item[segment]
    return item


def set_at(config, path, value):
    *parent_path, key = path
    item = config
    for segment in parent_path:
        if isinstance(item, list):
            item = item[int(segment)]
        else:
            item = item.setdefault(segment, {})
    if isinstance(item, list):
        item[int(key)] = value
    else:
        item[key] = value


def remove_at(config, path):
    *parent_path, key = path
    try:
        del item_at(config, parent_path)[key]
    except (KeyError, IndexError):
        raise ParametersError(f"Cannot remove missing path: {'.'.join(path)}") from None


def apply_block(config, block, params, aliases):
    for dotted_path, value in block.get("overrides", {}).items():
        path = tuple(dotted_path.split("."))
        value = substitute(value, params)
        for aliased_path in aliases.get(path, (path,)):
            set_at(config, aliased_path, value)
    for dotted_path in block.get("remove", []):
        remove_at(config, tuple(dotted_path.split(".")))


def generate_configs(parameters, configs_dir):
    """Yield the (path, config) pair of every experiment."""
    templates = {}
    for name, template_path in parameters["templates"].items():
        template_file = Path(configs_dir) / template_path
        with open(template_file) as file:
            templates[name] = (yaml.safe_load(file), aliased_paths(template_file))

    common = parameters.get("common", {})
    run_name_path = tuple(parameters["run_name_path"].split("."))

    for algorithm_name, algorithm in parameters["algorithms"].items():
        template, aliases = templates[algorithm["template"]]
        environment_names = algorithm.get("environments", list(parameters["environments"]))
        for environment_name in environment_names:
            environment = parameters["environments"][environment_name]
            for seed in parameters["seeds"]:
                params = {"env": environment_name, "seed": seed}
                for block in (common, environment, algorithm):
                    params.update(block.get("params", {}))

                config = copy.deepcopy(template)
                for block in (common, environment, algorithm):
                    try:
                        apply_block(config, block, params, aliases)
                    except ParametersError as error:
                        raise ParametersError(
                            f"{algorithm_name} on {environment_name}: {error}"
                        ) from None

                name = algorithm["filename"].format(
                    **{key: as_string(value) for key, value in params.items()}
                )
                for aliased_path in aliases.get(run_name_path, (run_name_path,)):
                    set_at(config, aliased_path, name)

                yield Path(algorithm["directory"]) / environment_name / f"{name}.yaml", config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parameters",
        "-p",
        type=Path,
        default=DEFAULT_PARAMETERS_FILE,
        help="Path to the file describing the experiments",
    )
    parser.add_argument(
        "--configs-dir",
        "-c",
        type=Path,
        default=DEFAULT_CONFIGS_DIR,
        help="Path to the configs directory holding the templates",
    )
    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="Print the name of every config instead of writing it",
    )
    args = parser.parse_args()

    with open(args.parameters) as file:
        parameters = yaml.safe_load(file)

    try:
        configs = list(generate_configs(parameters, args.configs_dir))
    except ParametersError as error:
        sys.exit(f"Error: {error}")

    for config_path, config in configs:
        if args.list:
            print(config_path.with_suffix(""))
            continue
        output_file = args.configs_dir / config_path
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w") as file:
            yaml.safe_dump(config, file, sort_keys=False, default_flow_style=None, width=120)

    if not args.list:
        print(f"Wrote {len(configs)} config files under {args.configs_dir}")


if __name__ == "__main__":
    main()
