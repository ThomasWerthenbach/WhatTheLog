"""
Author: Thomas Werthenbach
Email: t.a.k.werthenbach@student.tudelft.nl
"""

import multiprocessing
import os
import random
import shutil
import sys
from copy import deepcopy
from datetime import timedelta
from time import time
from typing import List, Dict, Tuple

import numpy as np

from scripts.log_scrambler import produce_false_trace
from whatthelog.definitions import PROJECT_ROOT
from whatthelog.prefixtree.edge_properties import EdgeProperties
from whatthelog.prefixtree.graph import Graph
from whatthelog.prefixtree.prefix_tree_factory import PrefixTreeFactory
from whatthelog.prefixtree.state import State
from whatthelog.syntaxtree.syntax_tree import SyntaxTree
from whatthelog.syntaxtree.syntax_tree_factory import SyntaxTreeFactory


class MarkovChain:
    """
    This class is used compress a model generated by log statements by using a Markov chain.
    """

    def __init__(self, traces_dir: str, config_file: str = "resources/config.json", weight_size: float = 0.5,
                 weight_accuracy: float = 0.5, eval_file: str = None, random_candidates: bool = False):
        self.maxLength = 0
        self.traces_dir = traces_dir
        self.config_file = config_file
        self.false_traces = None
        self.true_traces = None

        self.random_candidates = random_candidates

        self.eval_file = eval_file
        if eval_file and not os.path.exists(eval_file):
            with open(eval_file, 'w+') as open_file:
                open_file.write('')

        self.weight_size = weight_size
        self.weight_accuracy = weight_accuracy

        # Parse the syntax_tree from the config file.
        self.syntax_tree: SyntaxTree = SyntaxTreeFactory().parse_file(self.config_file)
        all_states: List[str] = self.get_all_states(self.syntax_tree)
        self.pt = None

        # Give every state an id.
        self.states: Dict[str, int] = dict()
        self.states['root'] = 0
        self.states['terminal'] = 1
        for a in range(len(all_states)):
            if all_states[a] in self.states:
                raise Exception(all_states[a])
            self.states[all_states[a]] = a + 2

        # Initialize the transition matrix for the root and terminal node.
        self.transitionMatrix = [[0 for _ in range(len(self.states))]
                                 for _ in range(len(self.states))]

        self.initial_size = len(self.transitionMatrix)

        print('[ masm.py ] - Chain ready.')

    def generate_false_traces_from_prefix_tree(self, false_dir, amount: int = 50) -> None:
        """
        This method will generate negative/false traces on a pre-specified location.
        It will also initialize a prefix tree.
        :param amount: Specifies the amount of negative/false traces to produce
        :param false_dir: Specifies the directory in which the false traces should temporarily be stored.
        """
        self.pt = PrefixTreeFactory.get_prefix_tree(self.traces_dir, self.config_file)
        print('[ masm.py ] - Generating false traces...')
        self.generate_false_traces(false_dir, amount)
        print('[ masm.py ] - False traces generated')
        self.pt = None

    def generate_false_traces(self, false_dir, amount: int = 50) -> None:
        """
        This method will generate negative/false traces on a pre-specified location.
        :param amount: Specifies the amount of negative/false traces to produce.
        :param false_dir: Specifies the directory in which the false traces should temporarily be stored.
        """
        for i in range(amount):
            produce_false_trace(os.path.join(self.traces_dir, os.listdir(self.traces_dir)
            [random.randint(0, len(os.listdir(self.traces_dir)) - 1)]),
                                false_dir + '/false' + str(i), self.syntax_tree, self.pt)
        self.false_traces = self.shorten_eval_traces_and_store_in_memory(false_dir)

    def get_all_states(self, syntax_tree: SyntaxTree) -> List[str]:
        """
        This method retrieves all the states from a SyntaxTree.
        :param syntax_tree: Specifies the tree of which the states should be retrieved.
        """
        res = []
        if len(syntax_tree.get_children()) > 0:
            for c in syntax_tree.get_children():
                res += self.get_all_states(c)
        else:
            return [syntax_tree.name]
        return res

    def remove(self, new: int, to_delete: int, matrix=None, states=None) -> None:
        """
        Removes a specific state from the transition matrix. It merges the probabilities into another state.
        :param new: The state in which the to-be-deleted state will be merged.
        :param to_delete: The state which will be removed. Its probabilities will be merged into 'new'.
        :param matrix: Specifies the transition matrix from which to remove the states.
        :param states: Specifies the states dictionary which should be modified.
        """
        if new == to_delete:
            raise Exception('trying to delete self')

        if matrix is None:
            matrix = self.transitionMatrix
        if states is None:
            states = self.states

        for i in range(len(matrix)):
            matrix[new][i] += matrix[to_delete][i]
            matrix[new][i] /= 2  # Normalize again

        del matrix[to_delete]

        for key in states.copy():
            if states[key] == to_delete:
                states[key] = new
            elif states[key] > to_delete:
                states[key] -= 1

        for arr in matrix:
            arr[new] += arr[to_delete]
            del arr[to_delete]

    def parallel(self, files: List[str]) -> List[List[float]]:
        """
        This method is used for parallel training of the transition matrix.
        :param files: Specifies the files this process should train the matrix on.
        """
        for file in files:
            with open(self.traces_dir + file, 'r') as open_file:
                current = 'root'
                for line in open_file.readlines():
                    try:
                        next_node = self.syntax_tree.search(line).name
                    except AttributeError as e:
                        print(line)
                        raise e
                    if next_node not in self.states:
                        raise Exception('Node not found in self.states')
                    self.transitionMatrix[self.states[current]][self.states[next_node]] += 1
                    current = next_node
                self.transitionMatrix[self.states[current]][self.states['terminal']] += 1
        return self.transitionMatrix

    def delete_unreachable(self) -> None:
        """
        Removes any unreachable states from the transition matrix as a form of initial compression.
        """
        to_delete: List[int] = list()
        for c in range(len(self.transitionMatrix)):
            if c > 0:  # the root state is an exception
                s = 0
                for r in self.transitionMatrix:
                    s += r[c]
                if s == 0:
                    to_delete.append(c)

        to_delete.sort(reverse=True)
        for d in to_delete:
            for key in self.states.copy():
                if self.states[key] == d:
                    del self.states[key]
                elif self.states[key] > d:
                    self.states[key] -= 1
            del self.transitionMatrix[d]
            for r in self.transitionMatrix:
                del r[d]

    def train(self) -> None:
        """
        Trains the probabilities of the transition matrix based on a pre-specified set of log files.
        """
        print('[ masm.py ] - Start training using multiprocessing...')

        a_pool = multiprocessing.Pool()
        matrix = None
        for result in a_pool.map(self.parallel, np.array_split(os.listdir(self.traces_dir), 12)):
            if matrix is None:
                matrix = result
            else:
                for r in range(len(result)):
                    for c in range(len(result)):
                        matrix[r][c] += result[r][c]
        a_pool.close()
        a_pool.join()
        self.transitionMatrix = matrix
        print('[ masm.py ] - Training done.')

        print('[ masm.py ] - Start Normalizing...')
        for i in range(len(self.transitionMatrix)):
            row = self.transitionMatrix[i]
            s = sum(row)
            if s > 0:
                for a in range(len(row)):
                    self.transitionMatrix[i][a] /= s
        print('[ masm.py ] - Normalizing done.')

        # Delete the unreachable states
        self.delete_unreachable()

        self.initial_size = len(self.transitionMatrix)

        self.transitionMatrix[1][1] = 1  # Create a self loop for the terminal state

    def find_duplicates(self, threshold: float = 0.0, row_duplicates: bool = True) -> List[List[int]]:
        """
        This method searches for either duplicate rows or columns, based on the value of 'row_duplicates'.
        :param threshold: This value indicates the maximum offset of two rows in order to be considered 'equivalent'.
        :param row_duplicates: Indicates whether we are looking for row duplicates or column duplicates.
        """
        result = list()

        for row in range(len(self.transitionMatrix)):
            to_append = [row]
            for i in range(len(self.transitionMatrix)):
                if row != i:

                    # Check if we have not already selected this row as candidate
                    found = False
                    for a in result:
                        if i in a:
                            found = True
                            break
                    if not found:
                        equivalent = True
                        for pos in range(len(self.transitionMatrix[i])):
                            if (row_duplicates and
                                abs(self.transitionMatrix[i][pos] - self.transitionMatrix[row][pos]) > threshold) \
                                    or \
                                    (not row_duplicates and
                                     abs(self.transitionMatrix[pos][i] - self.transitionMatrix[pos][row]) > threshold):
                                equivalent = False
                                break
                        if equivalent:
                            to_append.append(i)
            if len(to_append) > 1:
                result.append(to_append)

        return result

    def find_prop_1(self, threshold: float = 0.0) -> List[List[int]]:
        """
        Searches for entries in the transition matrix which have a probability of 1 of going to a certain state.
        :param threshold: indicates the amount of which a value is allowed to diverge from 1 in order to be considered.
        """
        temporary_result = list()

        for r in range(len(self.transitionMatrix)):
            for d in range(len(self.transitionMatrix)):
                # We don't want to remove the root and terminal node
                if self.transitionMatrix[r][d] >= 1.0 - threshold and r != d:
                    temporary_result.append([r, d])  # Only one, otherwise things might break
                    break

        result = list()
        for i in temporary_result:
            new = True
            for r in result:
                if r[-1] == i[0] and i[1] not in r:
                    r.append(i[1])
                    new = False
                    break
            if new:
                result.append(i)

        return result

    def build_graph(self) -> Graph:
        """
        Builds a Graph object based on the current states dictionary and transition matrix.
        """
        graph_nodes = dict()
        graph_nodes[0] = State(['root'])
        graph_nodes[1] = State(['terminal'])
        graph = Graph(graph_nodes[0], graph_nodes[1])
        for k, v in self.states.items():
            if k != 'root' and k != 'terminal':
                graph_nodes[v] = State([k])
                graph.add_state(graph_nodes[v])

        for r in range(len(self.transitionMatrix)):
            for c in range(len(self.transitionMatrix)):
                if self.transitionMatrix[r][c] > 0:
                    graph.add_edge(graph_nodes[r], graph_nodes[c], EdgeProperties([str(self.transitionMatrix[r][c])]))
        return graph

    def process_candidate_list(self, candidate: List[int], matrix=None, states=None) -> None:
        """
        Merges the states of a candidate for compression.
        :param candidate: The list of states to be merged, also known as the current candidate.
        :param matrix: The transition matrix on which the candidate should be applied.
        :param states: The states dictionary on which the candidate should be applied.
        """
        current = candidate.pop(0)
        while len(candidate) > 0:
            next_node = candidate.pop(0)
            self.remove(current, next_node, matrix, states)
            for a in range(len(candidate)):
                candidate[a] -= 1

    def evaluate_candidate(self, candidate: List[int]) -> Tuple[float, Tuple[float, float, float]]:
        """
        Evaluates the score of a candidate. This score is based on conciseness and accuracy.
        :param candidate: The candidate which should be evaluated
        """
        if self.weight_size * len(candidate) / self.maxLength + self.weight_accuracy <= self.weight_size:
            # Optimal accuracy for this candidate vs the worst accuracy for the candidate with most compression
            return 0, (0, 0, 0)
        else:
            matrix = deepcopy(self.transitionMatrix)
            states = deepcopy(self.states)
            current_length = len(candidate)
            self.process_candidate_list(candidate, matrix, states)
            score = self.calculate_accuracy(matrix, states)
            specificity, recall, _ = score
            return self.weight_size * current_length / self.maxLength + self.weight_accuracy * \
                   np.mean([specificity, recall]), score

    def calculate_accuracy(self, matrix: List[List[float]] = None, states: Dict[str, int] = None) \
            -> Tuple[float, float, float]:
        """
        Evaluates the specificity, recall and precision of a transition matrix.
        Specificity is calculated as: true negatives / (true negatives + false positives)
        Recall is calculated as:      true positives / (true positives + false negatives)
        Precision is calculated as:   true positives / (true positives + false positives)
        :param matrix: The transition matrix which should be evaluated.
        :param states: The states dictionary which should be evaluated.
        """
        if matrix is None:
            matrix = self.transitionMatrix
        if states is None:
            states = self.states

        true_negative = 0
        for trace in self.false_traces:
            current = states['root']
            for state in trace:
                if state not in states:
                    true_negative += 1
                    break
                next_node = states[state]
                if matrix[current][next_node] < 1 / 1e10:  # Some small value close to 0
                    true_negative += 1
                    break
                current = next_node

        true_positive = 0
        for trace in self.true_traces:
            is_valid = True
            current = states['root']
            for state in trace:
                if state not in states:
                    is_valid = False
                    break
                next_node = states[state]
                if matrix[current][next_node] < 1 / 1e10:  # Some small value close to 0
                    is_valid = False
                    break
                current = next_node
            if is_valid:
                true_positive += 1

        specificity = true_negative / len(self.false_traces)
        recall = true_positive / len(self.true_traces)
        precision = true_positive / (true_positive + (len(self.false_traces) - true_negative))

        return specificity, recall, precision

    def get_candidates(self, threshold: float = 0.0) -> List[List[int]]:
        """
        Retrieves all candidates based on a threshold.
        :param threshold: The threshold of which candidates can converge from the requirements.
        """
        if self.random_candidates:
            result = list()
            result.append([random.randint(0, len(self.transitionMatrix) - 1)])
            next_candidate = random.randint(0, len(self.transitionMatrix) - 1)
            while next_candidate == result[0][0]:
                next_candidate = random.randint(0, len(self.transitionMatrix) - 1)
            result[0].append(next_candidate)
            return result

        assert self.random_candidates is False

        # Duplicate rows
        candidates = list(map(lambda x: sorted(x), self.find_duplicates(threshold)))
        # Duplicate columns
        candidates += list(map(lambda x: sorted(x), list(
            filter(lambda x: sorted(x) not in candidates, self.find_duplicates(threshold, False)))))
        # Probability of 1
        candidates += list(map(lambda x: sorted(x), list(
            filter(lambda x: sorted(x) not in candidates, self.find_prop_1(threshold)))))

        return candidates

    def compress(self, minimum_size: int, minimum_accuracy: float, store_intermediate: bool = False) -> None:
        """
        Compresses a transition matrix until either the minimum size or minimum accuracy is reached.
        :param minimum_size: The minimum size of the matrix. The transition matrix should not become smaller than this.
        :param minimum_accuracy: The minimum accuracy of the matrix. The transition should not become less accurate than
                                 this.
        :param store_intermediate: Indicates whether intermediate results should be stored
        """
        assert minimum_size > 0, 'Can not have a size of 0'

        previous_matrix = deepcopy(self.transitionMatrix)
        current_accuracy = 1.0

        while len(self.transitionMatrix) > minimum_size and current_accuracy > minimum_accuracy:
            # Store the current matrix which is known to have the proper requirements
            previous_matrix = deepcopy(self.transitionMatrix)

            # Print the progress of our current compression in the console
            print(str(100 * (self.initial_size - len(self.transitionMatrix)) / (self.initial_size - minimum_size)), '%')

            threshold = 0.0
            candidates = []
            while len(candidates) == 0:  # Search for candidates until some are found
                candidates = self.get_candidates(threshold)
                threshold += 0.001  # Higher threshold in order to find more candidates

            to_split = []
            for c in range(len(candidates)):
                # Split candidates of which we know will make the resulting transition matrix too small.
                if len(candidates[c]) - 1 > len(self.transitionMatrix) - minimum_size:
                    to_split.append(c)
            # We reverse the list to make sure the indexes in to_split do not need to be shifted when deleting from
            # candidates.
            to_split.reverse()
            for s in to_split:
                candidate = candidates[s]
                i = 1
                while i < len(candidate):
                    candidates.append([candidate[i - 1], candidate[i]])
                    i += 1
                del candidates[s]

            # Find the largest candidate (for evaluating the conciseness of a merge)
            self.maxLength = 0
            for c in candidates:
                # The candidates are sorted in order to make sure that the merging of states works properly.
                # The smallest index should be the first element.
                c.sort()
                if len(c) > self.maxLength:
                    self.maxLength = len(c)

            max_index = 0
            results = []
            # We only want to evaluate the candidates if there are more than 1
            if len(candidates) > 1:
                a_pool = multiprocessing.Pool()
                # Evaluate all the candidates using multiprocessing
                results = a_pool.map(self.evaluate_candidate, candidates)
                a_pool.close()
                a_pool.join()

                # Pick the best candidate
                for r in range(len(candidates)):
                    if results[r][0] > results[max_index][0]:
                        max_index = r

            self.process_candidate_list(candidates[max_index])
            if results:
                score = results[max_index][1]
            else:
                score = self.calculate_accuracy()
            current_accuracy = np.mean(score)
            if self.eval_file and store_intermediate:
                with open(self.eval_file, 'a') as file:
                    file.write('<tr><td>' + str(1 - len(self.transitionMatrix) / self.initial_size) + '</td>')
                    file.write('<td>' + str(score[0]) + '</td>')
                    file.write('<td>' + str(score[1]) + '</td>')
                    file.write('<td>' + str(score[2]) + '</td>')
                    file.write('<td>' + str(timedelta(seconds=time() - start_time).seconds) + '.' +
                               str(timedelta(seconds=time() - start_time).microseconds) + '<td></tr>\n')

        if len(self.transitionMatrix) < minimum_size or current_accuracy < minimum_accuracy:
            self.transitionMatrix = previous_matrix

    def print_matrix(self, matrix=None):
        """
        Prints the matrix in a human-readable format.
        :param matrix: The matrix which to print.
        """
        if matrix is None:
            matrix = self.transitionMatrix
        print(len(matrix))
        for r in range(len(matrix)):
            # row = str(r) + ": "
            for c in range(len(matrix)):
                if matrix[r][c] > 0:
                    print(r, ',', c, ':', matrix[r][c])

        [print(i, aaa) for aaa, i in self.states.items()]

    def select_true_traces(self, true_test_dir):
        """
        Selects random true traces on which the model will be evaluated.
        :param true_test_dir: the directory which contains true traces.
        """
        print('[ masm.py ] - Start selecting true traces...')
        self.true_traces = self.shorten_eval_traces_and_store_in_memory(true_test_dir)
        print('[ masm.py ] - Selecting true traces done.')

    def shorten_eval_traces_and_store_in_memory(self, directory: str) -> List[List[str]]:
        """
        Shortens the evaluation traces. These traces often contain large blocks of duplicate log statements. This
        method will modify these traces such that there will no longer be more than 2 equivalent subsequent statements
        (the number 2 ensures that self-loops will still occur).

        As the Markov chain is already trained when the model is evaluated, it will not change the results. This method
        will only improve this algorithm's efficiency.
        """
        traces = []
        for file in os.listdir(directory):
            new_lines = []

            with open(os.path.join(directory, file), 'r') as f:
                previous = None
                for line in f.readlines():
                    current = self.syntax_tree.search(line).name
                    if previous != current:
                        previous = None
                        new_lines.append(current.strip())
                        if len(new_lines) >= 2 and new_lines[len(new_lines) - 2] == current:
                            previous = current

            os.remove(os.path.join(directory, file))
            if len(new_lines) >= 1:  # It is possible to that the only line was deleted due to mutations.
                traces.append(new_lines)
        return traces

    def run_test(self, true_test_dir,
                 false_dir, store_intermediate=False):
        """
        Runs a test and stores the results in specified file.
        :param true_test_dir: The directory in which a subset of the files in true_dir will be stored temporarily.
        :param false_dir: The directory in which false traces will be stored temporarily.
        :param store_intermediate: Indicates whether intermediate results should be stored.
        """
        self.train()
        amount = len(os.listdir(true_test_dir))
        self.select_true_traces(true_test_dir=true_test_dir)
        self.generate_false_traces_from_prefix_tree(false_dir=false_dir, amount=amount)
        self.compress(1, -1, store_intermediate)
        if not store_intermediate and self.eval_file:
            with open(self.eval_file, 'a') as evalfile:
                evalfile.write('<tr><td>' + str(amount) + '</td>')
                evalfile.write('<td>' + str(timedelta(seconds=time() - start_time).seconds) + '.' +
                               str(timedelta(seconds=time() - start_time).microseconds) + '</td></tr>\n')


def move_folds(all_log, test, source_dir, train_dir, test_dir):
    if os.path.exists(os.path.join(PROJECT_ROOT, test_dir)):
        shutil.rmtree(os.path.join(PROJECT_ROOT, test_dir))
    os.mkdir(os.path.join(PROJECT_ROOT, test_dir))
    for file in test:
        shutil.copy(os.path.join(PROJECT_ROOT, source_dir, file),
                    os.path.join(os.path.join(PROJECT_ROOT, test_dir, file)))

    if os.path.exists(os.path.join(PROJECT_ROOT, train_dir)):
        shutil.rmtree(os.path.join(PROJECT_ROOT, train_dir))
    os.mkdir(os.path.join(PROJECT_ROOT, train_dir))

    train_files = set(all_log) - set(test)
    assert len(train_files) == 800

    for file in train_files:
        shutil.copy(os.path.join(PROJECT_ROOT, source_dir, file),
                    os.path.join(os.path.join(PROJECT_ROOT, train_dir, file)))


if __name__ == '__main__':
    if len(sys.argv) != 2 or not sys.argv[1].startswith('--type=') or sys.argv[1].split('=')[1] not in ['accuracy',
                                                                                                        'runtime',
                                                                                                        'random']:
        print('Usage:\n'
              '    --type=[TYPE]    Indicates the type of evaluation to be performed.\n'
              '                     TYPE can be either: ''accuracy'', ''runtime'', or ''random.\n')
        sys.exit(0)

    if not os.path.exists(os.path.join(PROJECT_ROOT, 'out/')):
        os.mkdir(os.path.join(PROJECT_ROOT, 'out/'))
    if not os.path.exists(os.path.join(PROJECT_ROOT, 'out/eval')):
        os.mkdir(os.path.join(PROJECT_ROOT, 'out/eval'))
    if not os.path.exists(os.path.join(PROJECT_ROOT, 'out/false_traces')):
        os.mkdir(os.path.join(PROJECT_ROOT, 'out/false_traces'))

    if sys.argv[1].split('=')[1] == 'accuracy':
        for index, seed in enumerate([5, 6, 7]):
            random.seed(seed)
            for j in range(5):
                # Create train and test set
                all_logs = os.listdir(os.path.join(PROJECT_ROOT, 'resources/traces' + str(j + 1) + '/'))
                split_logs = np.array_split(all_logs, 5)  # 5 folds for k-fold cross validation
                for test_set in split_logs:
                    move_folds(all_logs, test_set, source_dir='resources/traces' + str(j + 1) + '/',
                               train_dir='resources/train_files/', test_dir='resources/test_files/')
                    print('current progress accuracy:', j + 1, '/ 10, and', index + 1, '/ 5')

                    start_time = time()
                    chain = MarkovChain(os.path.join(PROJECT_ROOT, 'resources/train_files/'),
                                        eval_file=os.path.join(PROJECT_ROOT, 'out/eval/accuracy_results'),
                                        config_file=os.path.join(PROJECT_ROOT, 'resources/config.json'))
                    chain.run_test(true_test_dir=os.path.join(PROJECT_ROOT, 'resources/test_files/'),
                                   false_dir=os.path.join(PROJECT_ROOT, 'out/false_traces/'),
                                   store_intermediate=True)

    elif sys.argv[1].split('=')[1] == 'runtime':
        random.seed(5)
        for index, length in enumerate([100, 250, 500, 750, 1000]):
            for j in range(5):
                for take in range(2):  # Twice for every dataset.
                    print('current progress runtime:', j + take + 1, '/ 10, and', index + 1, '/ 5')

                    # Selecting random training files
                    if os.path.exists(os.path.join(PROJECT_ROOT, 'resources/train_files/')):
                        shutil.rmtree(os.path.join(PROJECT_ROOT, 'resources/train_files/'))
                    os.mkdir(os.path.join(PROJECT_ROOT, 'resources/train_files/'))
                    for number in range(length):
                        shutil.copy(os.path.join(PROJECT_ROOT, 'resources/traces' + str(j + 1) + '/',
                                                 os.listdir(os.path.join(PROJECT_ROOT, 'resources/traces' + str(j + 1) + '/'))
                                                 [random.randint(0, len(os.listdir(
                                                         os.path.join(PROJECT_ROOT, 'resources/traces' + str(j + 1) + '/'))) - 1)]),
                                    os.path.join(
                                        os.path.join(PROJECT_ROOT, 'resources/train_files/trace' + str(number))))

                    # Selecting random test files
                    if os.path.exists(os.path.join(PROJECT_ROOT, 'resources/test_files/')):
                        shutil.rmtree(os.path.join(PROJECT_ROOT, 'resources/test_files/'))
                    os.mkdir(os.path.join(PROJECT_ROOT, 'resources/test_files/'))
                    for number in range(length):
                        shutil.copy(os.path.join(PROJECT_ROOT, 'resources/traces' + str(j + 1) + '/',
                                                 os.listdir(os.path.join(PROJECT_ROOT, 'resources/traces' + str(j + 1) + '/'))
                                                 [random.randint(0, len(os.listdir(
                                                         os.path.join(PROJECT_ROOT, 'resources/traces' + str(j + 1) + '/'))) - 1)]),
                                    os.path.join(
                                        os.path.join(PROJECT_ROOT, 'resources/test_files/trace' + str(number))))

                    start_time = time()
                    chain = MarkovChain(os.path.join(PROJECT_ROOT, 'resources/train_files/'),
                                        eval_file=os.path.join(PROJECT_ROOT, 'out/eval/runtime_results'),
                                        config_file=os.path.join(PROJECT_ROOT, 'resources/config.json'))
                    chain.run_test(true_test_dir=os.path.join(PROJECT_ROOT, 'resources/test_files/'),
                                   false_dir=os.path.join(PROJECT_ROOT, 'out/false_traces/'))
    elif sys.argv[1].split('=')[1] == 'random':
        for index, seed in enumerate([5, 6, 7]):
            random.seed(seed)
            for j in range(5):
                # Create train and test set
                all_logs = os.listdir(os.path.join(PROJECT_ROOT, 'resources/traces' + str(j + 1) + '/'))
                split_logs = np.array_split(all_logs, 5)  # 5 folds for k-fold cross validation
                for test_set in split_logs:
                    move_folds(all_logs, test_set, source_dir='resources/traces' + str(j + 1) + '/',
                               train_dir='resources/train_files/', test_dir='resources/test_files/')
                    print('current progress accuracy:', j + 1, '/ 5, and', index + 1, '/ 3')

                    start_time = time()
                    chain = MarkovChain(os.path.join(PROJECT_ROOT, 'resources/train_files/'),
                                        eval_file=os.path.join(PROJECT_ROOT, 'out/eval/random_results'),
                                        config_file=os.path.join(PROJECT_ROOT, 'resources/config.json'),
                                        random_candidates=True)

                    chain.run_test(true_test_dir=os.path.join(PROJECT_ROOT, 'resources/test_files/'),
                                   false_dir=os.path.join(PROJECT_ROOT, 'out/false_traces/'),
                                   store_intermediate=True)
    else:
        print('Operation not recognized')
