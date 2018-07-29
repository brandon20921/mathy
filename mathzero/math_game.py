import random
import math
import numpy
from .core.expressions import (
    MathExpression,
    ConstantExpression,
    STOP,
    AddExpression,
    VariableExpression,
)
from .core.problems import ProblemGenerator
from .core.parser import ExpressionParser
from .core.util import termsAreLike, isAddSubtract
from .core.rules import (
    BaseRule,
    AssociativeSwapRule,
    CommutativeSwapRule,
    DistributiveFactorOutRule,
    DistributiveMultiplyRule,
    ConstantsSimplifyRule,
)
from .core.profiler import profile_start, profile_end
from .math_state import MathState


class MetaAction:
    def count_nodes(self, expression: MathExpression) -> int:
        count = 0
        found = -1

        def visit_fn(node, depth, data):
            nonlocal count, found
            if node.id == expression.id:
                found = count
            count = count + 1

        expression.getRoot().visitInorder(visit_fn)
        return count, found


class VisitBeforeAction(MetaAction):
    def canVisit(self, expression: MathExpression) -> bool:
        count, found = self.count_nodes(expression)
        return bool(found > 1 and count > 1)

    def visit(self, expression: MathExpression, focus_index: int):
        return focus_index - 1


class VisitAfterAction(MetaAction):
    def canVisit(self, expression: MathExpression) -> bool:
        count, found = self.count_nodes(expression)
        return bool(found >= 0 and found < count - 2)

    def visit(self, expression: MathExpression, focus_index: int):
        return focus_index + 1


class MathGame:
    """
    Implement a math solving game where players have two distinct win conditions
    that require different strategies for solving. The first win-condition is for 
    the player to execute the right sequence of actions to reduce a math expression
    to its most basic representation. The second is for the other player to expand 
    the simple representations into more verbose expressions.

    Ideally a fully-trained player will be able to both simplify arbitrary mathematical
    expressions, and expand upon arbitrary parts of expressions to generate more complex
    representations that can be used to expand on concepts that a user may struggle with.
    """

    width = 128
    verbose = False
    draw = 0.0001

    def __init__(self):
        self.problems = ProblemGenerator()
        self.parser = ExpressionParser()
        self.available_actions = [VisitAfterAction(), VisitBeforeAction()]
        self.available_rules = [
            CommutativeSwapRule(),
            DistributiveFactorOutRule(),
            DistributiveMultiplyRule(),
            AssociativeSwapRule(),
            ConstantsSimplifyRule(),
        ]

    def getInitBoard(self):
        """return a numpy encoded version of the input expression"""
        self.expression_str = self.problems.sum_and_single_variable(max_terms=4)
        # print("\n\n\t\tNEXT: {}".format(self.expression_str))
        if len(list(self.expression_str)) > MathGame.width:
            raise Exception(
                'Expression "{}" is too long for the current model to process. Max width is: {}'.format(
                    self.expression_str, MathGame.width
                )
            )
        board = MathState(MathGame.width).encode_board(self.expression_str)

        # NOTE: This is called for each episode, so it can be thought of like "onInitEpisode()"
        return board

    def getBoardSize(self):
        """return shape (x,y) of board dimensions"""
        # 2 columns per player, the first for turn data, the second for text inputs
        return (4, MathGame.width)

    def getActionSize(self):
        """Return number of all possible actions"""
        return len(self.available_rules) + len(self.available_actions)

    def getNextState(self, board, player, action, searching=False):
        """
        Input:
            board:     current board
            player:    current player (1 or -1)
            action:    action taken by current player
            searching: boolean set to True when called by MCTS

        Returns:
            nextBoard: board after applying action
            nextPlayer: player who plays in the next turn (should be -player)
        """
        b = MathState(MathGame.width)
        text, move_count, focus_index, _ = b.decode_player(board, player)
        # if not searching:
        #     print("gns: {}, {}".format(player, focus_index))
        expression = self.parser.parse(text)
        token = self.getFocusToken(expression, focus_index)
        actions = self.available_actions + self.available_rules
        operation = actions[action]
        # searching = False

        # Enforce constraints to keep training time and complexity down?
        # - can't commutative swap immediately to return to previous state.
        # - can't focus on the same token twice without taking a valid other
        #   action inbetween
        # TODO: leaving these ideas here, but optimization made them less necessary

        if isinstance(operation, BaseRule) and operation.canApplyTo(token):
            change = operation.applyTo(token.rootClone())
            root = change.end.getRoot()
            if not searching and MathGame.verbose:
                print("[{}] {}".format(move_count, change.describe()))
            out_board = b.encode_player(
                board, player, root, move_count + 1, focus_index
            )
        elif isinstance(operation, MetaAction):
            operation_result = operation.visit(expression, focus_index)
            if not searching and MathGame.verbose:
                direction = (
                    "behind" if isinstance(operation, VisitBeforeAction) else "ahead"
                )
                print(
                    "[{}] 👀 Looking {} at: {}".format(
                        move_count,
                        direction,
                        self.getFocusToken(expression, operation_result),
                    )
                )
            out_board = b.encode_player(
                board, player, expression, move_count + 1, operation_result
            )
        else:
            raise Exception(
                "\n\nPlayer: {}\n\tExpression: {}\n\tFocus: {}\n\tIndex: {}\n\tinvalid move selected: {}, {}".format(
                    player, expression, token, focus_index, action, type(operation)
                )
            )

        return out_board, player * -1

    def getFocusToken(
        self, expression: MathExpression, focus_index: int
    ) -> MathExpression:
        """Get the token that is `focus_index` from the left of the expression"""
        count = 0
        result = None

        def visit_fn(node, depth, data):
            nonlocal result, count
            result = node
            if count == focus_index:
                return STOP
            count = count + 1

        expression.visitInorder(visit_fn)
        return result

    def getValidMoves(self, board, player):
        """
        Input:
            board: current board
            player: current player

        Returns:
            validMoves: a binary vector of length self.getActionSize(), 1 for
                        moves that are valid from the current board and player,
                        0 for invalid moves
        """
        b = MathState(MathGame.width)

        expression_text, _, focus_index, _ = b.decode_player(board, player)
        expression = self.parser.parse(expression_text)
        token = self.getFocusToken(expression, focus_index)
        actions = [0] * self.getActionSize()
        count = 0
        # Meta actions first
        for action in self.available_actions:
            if action.canVisit(token):
                actions[count] = 1
            count = count + 1
        # Rules of numbers
        for rule in self.available_rules:
            if rule.canApplyTo(token):
                actions[count] = 1
            count = count + 1

        # print_list = self.available_actions + self.available_rules
        # [
        #     print(
        #         "Player{} action[{}][{}] = {}".format(
        #             player, i, bool(a), type(print_list[i]) if a != 0 else ""
        #         )
        #     )
        #     for i, a in enumerate(actions)
        # ]
        return actions

    def getGameEnded(self, board, player, searching=False):
        """
        Input:
            board:     current board
            player:    current player (1 or -1)
            searching: boolean that is True when called by MCTS simulation

        Returns:
            r: 0 if game has not ended. 1 if player won, -1 if player lost,
               small non-zero value for draw.
               
        """
        b = MathState(MathGame.width)
        expression_text, move_count, _, _ = b.decode_player(board, player)
        # if not searching:
        #     print("Expression = {}".format(expression_text))
        expression = self.parser.parse(expression_text)

        # It's over if the expression is reduced to a single constant
        if (
            isinstance(expression, ConstantExpression)
            and len(expression.getChildren()) == 0
            and expression.parent is None
        ):

            eval = self.parser.parse(self.expression_str).evaluate()
            found = expression.evaluate()
            if math.isclose(eval, found):
                if not searching:
                    print(
                        "[LOSE] ERROR: reduced '{}' to constant, but evals differ. Expected '{}' but got '{}'!".format(
                            self.expression_str, eval, found
                        )
                    )
                return -1

            # Holy shit it won!
            if not searching and MathGame.verbose:
                print(
                    "\n[Player{}][WIN] {} => {}!".format(
                        player, self.expression_str, expression
                    )
                )
            else:
                pass
                # print(".")

            return 1
        # Check for simplification down to a single addition with constant/variable
        add_sub = expression if isinstance(expression, AddExpression) else None
        if add_sub is None:
            find = expression.findByType(AddExpression)
            add_sub = find[0] if len(find) > 0 else add_sub

        if add_sub and add_sub.parent is None:
            constant = None
            variable = None

            if isinstance(add_sub.left, ConstantExpression):
                constant = add_sub.left
            elif isinstance(add_sub.right, ConstantExpression):
                constant = add_sub.right

            if isinstance(add_sub.left, VariableExpression):
                variable = add_sub.left
            elif isinstance(add_sub.right, VariableExpression):
                variable = add_sub.right

            # The game continues...
            if constant is not None and variable is not None:
                # TODO: Compare constant/variable findings to verify their accuracy.
                #       This helps until we're 100% confident in the parser/serializer consistency
                #
                # Holy shit it won!
                if not searching and MathGame.verbose:
                    print(
                        "\n[Player{}][TERMWIN] {} => {}!".format(
                            player, self.expression_str, expression
                        )
                    )
                    # print("TERM WIN WITH CONSTANT: {}".format(constant))
                    # print("TERM WIN WITH VARIABLE: {}".format(variable))
                return 1

        # Check the turn count last because if the previous move that incremented
        # the turn over the count resulted in a win-condition, it should be honored.
        if move_count > 20:
            e2, move_count_other, _, _ = b.decode_player(board, player * -1)
            if move_count_other > 20:
                if not searching and MathGame.verbose:
                    print(
                        "\n[DRAW] ENDED WITH:\n\t 1: {}\n\t 2: {}\n".format(
                            expression, e2
                        )
                    )
                return MathGame.draw

        # The game continues
        return 0

    # ===================================================================
    #
    #       STOP! THERE IS NOTHING YOU WANT TO CHANGE BELOW HERE.
    #
    # ===================================================================

    def getCanonicalForm(self, board, player):
        """
        Input:
            board: current board
            player: current player (1 or -1)

        Returns:
            canonicalBoard: returns canonical form of board. The canonical form
                            should be independent of player. For e.g. in chess,
                            the canonical form can be chosen to be from the pov
                            of white. When the player is white, we can return
                            board as is. When the player is black, we can invert
                            the colors and return the board.
        """
        # print("gcf: {}".format(player))
        return MathState(MathGame.width).get_canonical_board(board, player)

    def getSymmetries(self, board, pi):
        """
        Input:
            board: current board
            pi: policy vector of size self.getActionSize()

        Returns:
            symmForms: a list of [(board,pi)] where each tuple is a symmetrical
                       form of the board and the corresponding pi vector. This
                       is used when training the neural network from examples.
        """
        return [(board, pi)]

    def getPolicyKey(self, board):
        """conversion of board to a string format, required by MCTS for hashing."""
        b = MathState(MathGame.width)
        # This is always called for the canonical board which means the
        # current player is always in player1 slot:
        e1, m1, f1, _ = b.decode_player(board, 1)
        # Note that the focus technically has no bearing on the win-state
        # so we don't attach it to the cache for MCTS to keep the number
        # of keys down.
        # JDD: UGH without this the invalid move selection goes up because keys conflict.

        # TODO: Add player index here? Store in board data after focus_index? This controls valid moves
        #       could there be a problem with cache conflicts? Are the policies the same for each player
        #       given the m/f/e?

        return "[ {}, {}, {} ]".format(m1, f1, e1)

    def getEndedStateKey(self, board):
        """conversion of board to a string format, required by MCTS for hashing."""
        b = MathState(MathGame.width)
        # This is always called for the canonical board which means the
        # current player is always in player1 slot:
        e1, m1, _, _ = b.decode_player(board, 1)
        return "[ {}, {} ]".format(m1, e1)


def display(board, player):
    b = MathState(MathGame.width)
    expression = b.decode_player(board, player)[0]
    expression_len = len(expression)
    width = 100
    if player == 1:
        buffer = " " * int(width / 2 - expression_len)
    elif player == -1:
        buffer = " " * int(width - expression_len)
    else:
        raise ValueError("invalid player index: {}".format(player))
    print("{}{}".format(buffer, expression))