from ..expressions import (
    AddExpression,
    MultiplyExpression,
    ConstantExpression,
    VariableExpression,
    PowerExpression,
    MathExpression,
)
from ..rule import BaseRule


class CommutativeSwapRule(BaseRule):
    r"""Commutative Property
    For Addition: `a + b = b + a`

             +                  +
            / \                / \
           /   \     ->       /   \
          /     \            /     \
         a       b          b       a

    For Multiplication: `a * b = b * a`

             *                  *
            / \                / \
           /   \     ->       /   \
          /     \            /     \
         a       b          b       a
    """
    preferred: bool

    def __init__(self, preferred=True):
        # If false, terms that are in preferred order will not commute
        self.preferred = preferred

    @property
    def name(self):
        return "Commutative Swap"

    @property
    def code(self):
        return "CS"

    def can_apply_to(self, node):
        # Must be an add/multiply
        if isinstance(node, AddExpression):
            return True
        if not isinstance(node, MultiplyExpression):
            return False
        # When preferred is false, the commutative rule won't apply to term
        # nodes that are already in a preferred position.
        if self.preferred is False:
            # 4x won't commute to x * 4
            left_const = isinstance(node.left, ConstantExpression)
            right_var = isinstance(node.right, VariableExpression)
            if left_const and right_var:
                return False
            # 8y^4 won't commute to y^4 * 8
            if isinstance(node.right, PowerExpression):
                right_left_var = isinstance(node.right.left, VariableExpression)
                right_right_const = isinstance(node.right.right, ConstantExpression)
                if right_left_var and right_right_const:
                    return False
        return True

    def apply_to(self, node: MathExpression):
        change = super().apply_to(node)
        a = node.left
        b = node.right

        node.set_right(a)
        node.set_left(b)
        node.set_changed()

        change.done(node)
        return change
