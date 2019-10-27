import json
import math
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Union

import numpy as np

from .core.expressions import (
    AddExpression,
    BinaryExpression,
    ConstantExpression,
    DivideExpression,
    MathExpression,
    MultiplyExpression,
    NegateExpression,
    PowerExpression,
    SubtractExpression,
    VariableExpression,
)
from .core.parser import ExpressionParser
from .core.tree import LEFT


def is_debug_mode():
    """Debug mode enables extra logging and assertions, but is slower because of
    the increased sanity check measurements."""
    return False


def load_rule_tests(name):
    rule_file = Path(__file__).parent / "tests" / "rules" / "{}.json".format(name)
    print(rule_file)
    assert rule_file.is_file() is True
    with open(rule_file, "r") as file:
        return json.load(file)


def compare_expression_string_values(from_expression: str, to_expression: str):
    """Compare and evaluate two expressions strings to verify they have the
    same value"""
    parser = ExpressionParser()
    return compare_expression_values(
        parser.parse(from_expression), parser.parse(to_expression)
    )


def compare_expression_values(
    from_expression: MathExpression, to_expression: MathExpression
):
    """Compare and evaluate two expressions to verify they have the same value"""
    vars_from: set = set()
    vars_to: set = set()
    for v in from_expression.findByType(VariableExpression):
        vars_from.add(v.identifier)
    for v in to_expression.findByType(VariableExpression):
        vars_to.add(v.identifier)
    # If there are not the same unique vars in the two expressions, something
    # bad happened, and the two expressions can only coincidentally be equal
    # in value.
    assert len(vars_from) == len(
        vars_to
    ), f"unique variable count changed: ({len(vars_from)} != {len(vars_to)})"

    sorted_from = list(vars_from)
    sorted_from.sort()
    sorted_to = list(vars_from)
    sorted_to.sort()
    assert (
        sorted_from == sorted_to
    ), f"unique variables changed: ({sorted_from} != {sorted_to})"

    # Generate random values for each variable, and then evaluate the expressions
    eval_context: Dict[str, int] = {}
    for var in vars_from:
        eval_context[var] = np.random.randint(1, 100)

    value_from = from_expression.evaluate(eval_context)
    value_to = to_expression.evaluate(eval_context)

    assert (
        value_from == value_to
    ), f"expression value changed: ({value_from} != {value_to})"


def unlink(node: Optional[MathExpression] = None) -> Optional[MathExpression]:
    """Unlink an expression from it's parent.
      1. Clear expression references in `parent`
      2. Clear `parent` in expression
    """

    if node is None:
        return None

    if node.parent is not None and node == node.parent.left:
        node.parent.set_left(None)

    if node.parent is not None and node == node.parent.right:
        node.parent.set_right(None)

    node.parent = None
    return node


# Build a verbose factor dictionary.
#
# This builds a dictionary of factors for a given value that
# contains both arrangements of terms so that all factors are
# accessible by key.  That is, factoring 2 would return
#      result =
#        1 : 2
#        2 : 1
def factor(value) -> Dict[int, int]:
    if value == 0 or math.isnan(value):
        return {}

    sqrt = np.sqrt(value)
    if math.isnan(sqrt):
        return {}

    sqrt = int(sqrt + 1)
    factors = {1: value}
    factors[value] = 1

    for i in range(2, sqrt):
        if value % i == 0:
            one = i
            two = value / i
            factors[one] = two
            factors[two] = one
    return factors


def is_add_or_sub(node):
    return isinstance(node, AddExpression) or isinstance(node, SubtractExpression)


def get_sub_terms(node: MathExpression):
    nodes = node.toList("inorder")
    terms = []

    def safe_pop():
        nonlocal nodes
        if len(nodes) > 0:
            return nodes.pop(0)
        return None

    current = safe_pop()

    while current is not None:
        term_const = term_var = term_exp = None
        # If there's a coefficient, note it
        if isinstance(current, ConstantExpression):
            term_const = current
            current = safe_pop()
            # Cannot have add/sub in sub-terms
            if is_add_or_sub(current):
                return False
            # It should be one of these operators
            assert current is None or isinstance(
                current, (MultiplyExpression, DivideExpression, PowerExpression)
            )
            if not isinstance(current, PowerExpression):
                current = safe_pop()

        # Pop off the variable
        if isinstance(current, VariableExpression):
            term_var = current
            current = safe_pop()
            # cannot have add/sub in sub-terms
            if is_add_or_sub(current):
                return False
            # It should be one of these operators
            assert current is None or isinstance(
                current, (MultiplyExpression, DivideExpression, PowerExpression)
            )
            if not isinstance(current, PowerExpression):
                current = safe_pop()

        # Look for exponent
        if isinstance(current, PowerExpression):
            # pop the power binary expression
            current = safe_pop()
            # store the right hand side
            term_exp = current
            # pop it off for next term
            current = safe_pop()

        # Couldn't find anything
        if term_const is None and term_exp is None and term_var is None:
            # If it's a continuation multiply, e.g in "4 * (x^2 * z^6)"
            # NOTE: it's a continuation mult in the above expression
            #   because of the parens grouping between the 4 multiply
            #   and the inner multiply of the parens sub-terms
            if isinstance(current, MultiplyExpression):
                # pop it off for next term
                current = safe_pop()
                continue

            return False
        terms.append((term_const, term_var, term_exp))
    return terms


def is_simple_term(node: MathExpression) -> bool:
    """
    Return True if a given term has been simplified such that it only has at 
    most one of each variable and a constant.
    Example:
        Simple = 2x^2 * 2y
        Complex = 2x * 2x * 2y

        Simple = x^2 * 4
        Complex = 2 * 2x^2
    """
    sub_terms = get_sub_terms(node)
    if sub_terms is False:
        return False
    seen: set = set()
    co_key = "coefficient"

    for coefficient, variable, exponent in sub_terms:
        if coefficient is not None:
            if co_key in seen:
                return False
            seen.add(co_key)

        if variable is not None or exponent is not None:
            key = "{}{}".format(variable, exponent)
            if key in seen:
                return False
            seen.add(key)

    return True


def is_preferred_term_form(expression: MathExpression) -> bool:
    """
    Return True if a given term has been simplified such that it only has
    a max of one coefficient and variable, with the variable on the right
    and the coefficient on the left side
    Example:
        Complex   = 2 * 2x^2
        Simple    = x^2 * 4
        Preferred = 4x^2
    """
    if not is_simple_term(expression):
        return False

    # If there are multiple multiplications this term can be simplified further. At most
    # we expect a multiply to connect a coefficient and variable.
    # NOTE: the following check is removed because we need to handle multiple variable
    #       terms, e.g. "4x * z"
    # if len(expression.findByType(MultiplyExpression)) > 1:
    #     return False

    # if there's a variable, make sure the coefficient is on the left side
    # for the preferred compact form. i.e. "4x" instead of "x * 4"
    vars: List[VariableExpression] = expression.findByType(VariableExpression)
    seen_vars: Dict[str, int] = dict()
    parent: MathExpression
    for var in vars:
        if var.identifier not in seen_vars:
            seen_vars[var.identifier] = 0
        seen_vars[var.identifier] += 1
        parent = var
        if isinstance(var.parent, PowerExpression):
            parent = var.parent
        if parent.parent is not None and parent.parent.get_side(parent) == LEFT:
            if isinstance(parent.parent.right, ConstantExpression):
                return False
    # If any variables show up more than once, they can be combined.
    # examples:
    #  - `b * (44b^2)`
    #  - `z * (1274z^2)`
    for key, value in seen_vars.items():
        if value > 1:
            return False
    return True


def has_like_terms(expression: MathExpression) -> bool:
    """
    Return True if a given expression has more than one of any
           type of term.
    Examples:
        x + y + z = False
        x^2 + x = False
        y + 2x = True
        x^2 + 4x^3 + 2y = True
    """

    seen: set = set()
    term_nodes = get_terms(expression)
    for node in term_nodes:
        term = get_term(node)
        if term is False:
            continue
        var_key = ("".join(term.variables), term.exponent)
        # If the same var/power combinaton is found in the expression more than once
        # there are like terms.
        if var_key in seen:
            return True
        seen.add(var_key)

    # Look for multiple free-floating constants
    consts = expression.findByType(ConstantExpression)
    for const in consts:
        if const.parent and is_add_or_sub(const.parent):
            if "const_term" in seen:
                return True
            seen.add("const_term")
    return False


class FactorResult:
    def __init__(self):
        self.best = -1
        self.left = -1
        self.right = -1
        self.all_left = []
        self.all_right = []
        self.variable = None
        self.exponent = None
        self.leftExponent = None
        self.rightExponent = None
        self.leftVariable = None
        self.rightVariable = None


# Create a term node hierarchy from a given set of
# term parameters.  This takes into account removing
# implicit coefficients of 1 where possible.
def make_term(coefficient, variable, exponent):
    constExp = ConstantExpression(coefficient)
    if not variable and not exponent:
        return constExp

    varExp = VariableExpression(variable)
    if coefficient == 1 and not exponent:
        return varExp

    multExp = MultiplyExpression(constExp, varExp)
    if not exponent:
        return multExp

    expConstExp = ConstantExpression(exponent)
    if coefficient == 1:
        return PowerExpression(varExp, expConstExp)

    return PowerExpression(multExp, expConstExp)


class TermResult:
    def __init__(self):
        self.coefficients = []
        self.variables = []
        self.exponent = None
        self.node_coefficients = []
        self.node_variables = []
        self.node_exponent = None


# Extract term information from the given node
#
def get_term(node) -> TermResult:
    result = TermResult()
    # Constant with add/sub parent should be OKAY.
    if isinstance(node, ConstantExpression):
        if not node.parent or (node.parent and is_add_or_sub(node.parent)):
            result.coefficients = [node.value]
            result.node_coefficients = [node]
            return result

    # Variable with add/sub parent should be OKAY.
    if isinstance(node, VariableExpression):
        if not node.parent or (node.parent and is_add_or_sub(node.parent)):
            result.variables = [node.identifier]
            result.node_variables = [node]
            return result

    # TODO: Comment resolution on whether +- is OKAY, and if not, why it breaks down.
    if not is_add_or_sub(node):
        if (
            len(node.findByType(AddExpression)) > 0
            or len(node.findByType(SubtractExpression)) > 0
        ):
            return False

    # If another add is found on the left side of this node, and the right node
    # is _NOT_ a leaf, we cannot extract a term.  If it is a leaf, the term should be
    # just the right node.
    if node.left and len(node.left.findByType(AddExpression)) > 0:
        if node.right and not node.right.is_leaf():
            return False

    if node.right and len(node.right.findByType(AddExpression)) > 0:
        return False

    exponents = node.findByType(PowerExpression)
    if len(exponents) > 0:
        # Supports only single exponents in terms
        if len(exponents) != 1:
            return False

        exponent = exponents[0]
        if not isinstance(exponent.right, ConstantExpression):
            return False

        result.exponent = exponent.right.value
        result.node_exponent = exponent

    variables = node.findByType(VariableExpression)
    if len(variables) > 0:
        result.variables = [v.identifier for v in variables]
        result.variables.sort()
        result.node_variables = variables

    def filter_coefficients(n):
        if not n.parent or n.parent == node.parent:
            return True

        if isinstance(n.parent, BinaryExpression) and not isinstance(
            n.parent, MultiplyExpression
        ):
            return False
        return True

    coefficients = node.findByType(ConstantExpression)
    coefficients = [c for c in coefficients if filter_coefficients(c)]
    if len(coefficients) > 0:

        def resolve_coefficients(c):
            value = c.value
            if isinstance(c.parent, NegateExpression):
                value = value * -1

            return value

        result.coefficients = [resolve_coefficients(c) for c in coefficients]
        result.node_coefficients = coefficients

    empty = (
        len(result.variables) == 0
        and len(result.coefficients) == 0
        and result.exponent is None
    )
    if empty:
        return False

    # consistently return an empty coefficients/variables array in case none exist.
    # this ensures that you can always reference coefficients[0] or variables[0] and
    # check that for truthiness, rather than having to check that the object property
    # `coefficients` or `variables` is not None and also the truthiness of index 0.
    if not result.coefficients:
        result.coefficients = []

    if not result.variables:
        result.variables = []
    return result


class TermEx(NamedTuple):
    coefficient: Optional[Union[int, float]]
    variable: Optional[str]
    exponent: Optional[Union[int, float]]


def get_term_ex(node: Optional[MathExpression]) -> Optional[TermEx]:
    """Extract the 3 components of a naturally ordered term. This doesn't care
    about whether the node is part of a larger term, it only looks at its children.
    """

    # "4"
    if isinstance(node, ConstantExpression):
        # Make sure the parent isn't an exponent link (in which case we'd incorrectly
        # report this as a term with no exponent.) That case can be handled by calling
        # this on the parent node.
        if node.parent is None or not isinstance(node.parent, PowerExpression):
            return TermEx(node.value, None, None)

    # "x"
    if isinstance(node, VariableExpression):
        # Make sure the parent isn't an exponent link (in which case we'd incorrectly
        # report this as a term with no exponent.) That case can be handled by calling
        # this on the parent node.
        if node.parent is None or not isinstance(node.parent, PowerExpression):
            return TermEx(None, node.identifier, None)
    # "4 * ???"
    if isinstance(node, MultiplyExpression) and isinstance(
        node.left, ConstantExpression
    ):
        # "4x"
        if isinstance(node.right, VariableExpression):
            return TermEx(node.left.value, node.right.identifier, None)

        # "4x^2"
        if isinstance(node.right, PowerExpression):
            pow = node.right
            if isinstance(pow.left, VariableExpression) and isinstance(
                pow.right, ConstantExpression
            ):
                return TermEx(node.left.value, pow.left.identifier, pow.right.value)

    # "x^2"
    if isinstance(node, PowerExpression):
        if isinstance(node.left, VariableExpression) and isinstance(
            node.right, ConstantExpression
        ):
            return TermEx(None, node.left.identifier, node.right.value)

    return None


def factor_add_terms_ex(left_term: TermEx, right_term: TermEx):
    if not left_term or not right_term:
        raise ValueError("invalid terms for factoring")

    # Common coefficients
    l_factors = factor(
        left_term.coefficient if left_term.coefficient is not None else 1
    )
    r_factors = factor(
        right_term.coefficient if right_term.coefficient is not None else 1
    )
    common = [k for k in r_factors if k in l_factors]
    if len(common) == 0:
        return False
    has_left: bool = left_term.variable is not None
    has_right: bool = right_term.variable is not None

    # If there are variables, we want to extract them, so
    # the smallest number to factor out. TODO: is this okay?
    if has_left or has_right:
        best = np.min(common)
    else:
        best = np.max(common)
    result = FactorResult()
    result.best = best
    result.left = l_factors[best]
    result.right = r_factors[best]
    result.all_left = l_factors
    result.all_right = r_factors

    # Common variables and powers
    two_exp_and_match: bool = (
        left_term.exponent is not None
        and right_term.exponent is not None
        and left_term.exponent == right_term.exponent
    )
    both_match = (
        False
        if (left_term.exponent or right_term.exponent) and not two_exp_and_match
        else True
    )
    if (
        has_left
        and has_right
        and left_term.variable == right_term.variable
        and both_match
    ):
        result.variable = left_term.variable
        result.exponent = left_term.exponent

    if left_term.exponent and left_term.exponent != result.exponent:
        result.leftExponent = left_term.exponent

    if right_term.exponent and right_term.exponent != result.exponent:
        result.rightExponent = right_term.exponent

    if has_left and left_term.variable != result.variable:
        result.leftVariable = left_term.variable

    if has_right and right_term.variable != result.variable:
        result.rightVariable = right_term.variable

    return result


def get_terms(expression: MathExpression):
    results = []
    root = expression.get_root()
    if isinstance(root, MultiplyExpression):
        results.append(root)

    def visit_fn(node, depth, data):
        nonlocal results
        if not is_add_or_sub(node):
            return
        if not is_add_or_sub(node.left):
            results.append(node.left)
        if not is_add_or_sub(node.right):
            results.append(node.right)

    root.visit_inorder(visit_fn)
    return [expression] if len(results) == 0 else results


def terms_are_like(one, two):
    """
    @param {Object|MathExpression} one The first term {@link #get_term}
    @param {Object|MathExpression} two The second term {@link #get_term}
    @returns {Boolean} Whether the terms are like or not.
    """
    # Both must be valid terms
    if one is False or two is False:
        return False

    # Extract terms from MathExpressions if need be
    if isinstance(one, MathExpression):
        one = get_term(one)

    if isinstance(two, MathExpression):
        two = get_term(two)

    # if neither have variables, then they are a match!
    if len(one.variables) == 0 and len(two.variables) == 0:
        return True

    # Both must have variables, and they must match exactly.
    if len(one.variables) != len(two.variables):
        return False

    invalid = len([False for v in one.variables if v not in two.variables]) > 0
    if invalid:
        return False

    # Also, the exponents must match
    if one.exponent != two.exponent:
        return False

    # Same variables, and exponents.  Good to go.
    return True


# Negate helper

# `l - r = l + (-r)`
#
#             -                  +
#            / \                / \
#           /   \     ->       /   \
#          /     \            /     \
#         *       2          *       -
#        / \                / \       \
#       4   x              4   x       2
def negate(node):
    save = node.parent
    saveSide = save.get_side(node) if save is not None else None
    unlink(node)
    newNode = AddExpression(node.left, NegateExpression(node.right))
    if save is None:
        save.set_side(newNode, saveSide)

    return newNode


# Determine if an expression represents a constant term
def is_const(node):
    if isinstance(node, ConstantExpression):
        return True

    if isinstance(node, NegateExpression) and is_const(node.child):
        return True

    return False
