from typing import List, Union, Dict, Tuple

from whatthelog.auto_printer import AutoPrinter
from whatthelog.exceptions import StateAlreadyExistsException, \
    StateDoesNotExistException, NonDeterminismException
from whatthelog.prefixtree.edge_properties import EdgeProperties
from whatthelog.prefixtree.sparse_matrix import SparseMatrix
from whatthelog.prefixtree.state import State
from whatthelog.prefixtree.state_properties import StateProperties
from whatthelog.syntaxtree.syntax_tree import SyntaxTree


class Graph(AutoPrinter):
    """
    Class implementing a graph
    """

    __slots__ = ['edges', 'states', 'state_indices_by_id', 'prop_by_hash',
                 'start_node', 'terminal_node']

    def __getstate__(self):
        return {slot: getattr(self, slot) for slot in self.__slots__}

    def __setstate__(self, state):

        for slot in state:
            setattr(self, slot, state[slot])

        # --- Rebuild state indices table ---
        self.state_indices_by_id = {}
        for index, s in self.states.items():
            self.state_indices_by_id[id(s)] = index

    def __init__(self, start_node: State = None, terminal_node: State = None):
        self.edges = SparseMatrix()
        self.states: Dict[int, State] = {}
        self.state_indices_by_id: Dict[int, int] = {}
        self.prop_by_hash: Dict[int, StateProperties] = {}
        self.start_node = start_node
        self.terminal_node = terminal_node
        if start_node is not None:
            self.add_state(start_node)
        if terminal_node is not None:
            self.add_state(terminal_node)

    def get_state_by_id(self, state_id: int):
        """
        Method to fetch the state object from its hash.
        :param state_id: the hash of the state to fetch
        :return: the state object
        """
        if state_id not in self.state_indices_by_id:
            raise StateDoesNotExistException()
        return self.states[self.state_indices_by_id[state_id]]

    def add_state(self, state: State) -> None:
        """
        Method to add a new state to the graph.

        :param state: State to add
        :raises StateAlreadyExistsException: if state already exists
        """

        if state in self:
            raise StateAlreadyExistsException()

        curr_index = len(self.states)
        self.states[curr_index] = state
        self.state_indices_by_id[id(state)] = curr_index

        if state.properties.get_prop_hash() in self.prop_by_hash:
            state.properties = self.prop_by_hash[
                state.properties.get_prop_hash()]
        else:
            self.prop_by_hash[
                state.properties.get_prop_hash()] = state.properties

    def add_edge(self, start: State, end: State,
                 props: EdgeProperties = EdgeProperties()) -> bool:
        """
        Method to add an edge to the graph

        :param start: Origin state of the edge
        :param end: Destination state of the edge
        :param props: the edge properties object
        :return: Whether adding the edge was successful. If one of the nodes in
        edge does not exist or edge already exists returns False else True.
        """

        if id(start) not in self.state_indices_by_id or id(
                end) not in self.state_indices_by_id:
            return False

        start_index = self.state_indices_by_id[id(start)]
        end_index = self.state_indices_by_id[id(end)]
        if not (start_index, end_index) in self.edges:
            self.edges[start_index, end_index] = str(props)
            return True
        return False

    def size(self):
        """
        Method to get the size of the graph.

        :return: Number of states
        """
        return len(self.states)

    def get_outgoing_props(self, state: State) -> List[EdgeProperties]:
        """
        Method to get outgoing edges of a state.

        :param state: State to get outgoing edges for
        :return: List of outgoing edges from state.
        If state does not exist raises StateDoesNotExistException.
        """
        if state in self:
            results = self.edges.find_children(
                self.state_indices_by_id[id(state)])
            return [EdgeProperties.parse(result[1]) for result in results]
        else:
            raise StateDoesNotExistException

    def get_outgoing_states(self, state: State) -> List[State]:
        """
        Method to get outgoing states of a state.

        :param state: State to get outgoing states for
        :return: List of outgoing edges from state.
        If state does not exist raises StateDoesNotExistException.
        """
        if state in self:
            results = self.edges.find_children(
                self.state_indices_by_id[id(state)])
            return [self.states[result[0]] for result in
                    results] if results is not None else []
        else:
            raise StateDoesNotExistException()

    def get_outgoing_states_not_self(self, current: State) -> List[State]:
        """
        Retrieves the outgoing states of a given state, excluding itself in case of a self-loop (extra O(n)).
        :param current: The state of which we want to retrieve the outgoing states.
        :return: All the outgoing states, excluding itself in case of a self-loop. In this case that the state is not
                 present, we return an empty list
        """
        outgoing = self.get_outgoing_states(current)
        if outgoing:
            return list(filter(lambda x: x is not current, outgoing))
        else:
            return []

    def get_incoming_states(self, state: State):
        """
        Method to get outgoing states of a state.

        :param state: State to get outgoing states for
        :return: List of outgoing edges from state.
        If state does not exist raises StateDoesNotExistException.
        """
        if state in self:
            results = self.edges.get_parents(
                self.state_indices_by_id[id(state)])
            return [self.states[result] for result in
                    results] if results else []
        else:
            raise StateDoesNotExistException()

    def full_merge_states(self, s1: State, s2: State) -> State:
        """
        Fully merges two states and removes non-determinism.
        :param s1: One of the two states to merge.
        :param s2: One of the two states to merge.
        :return: resulting merged state
        """
        if s2 is None or s1 is None:
            return

        # Trivially merge the states
        self.merge_states(s1, s2)

        # Remove non-determinism in the merged state's children by merging them.
        new_state = self.determinize(s1)

        return new_state

    def full_merge_states_with_children(self, state: State, children_indices: List[int] = None) -> State:
        """
        Merges all the children of a state into the current state.

        :param state: the state to merge
        :return: the merged state
        """
        if state not in self:
            raise StateDoesNotExistException()
        if children_indices is not None:
            print(children_indices)
        # Trivially merge all children into target state.
        outgoing_states = self.get_outgoing_states_not_self(state)
        for i, outgoing in enumerate(outgoing_states):
            if children_indices is not None:
                if i in children_indices and outgoing.is_terminal is False:
                    self.merge_states(state, outgoing)
            else:
                self.merge_states(state, outgoing)

        # Remove non-determinism in the merged state's children by merging them.
        new_state = self.determinize(state)

        return new_state

    def merge_states(self, state1: State, state2: State) -> None:
        """
        This method merges two states and passes the properties from one state to the other.
        :param state1: The new 'merged' state
        :param state2: The state that will be deleted and which properties will be passed to state 1
        """
        props = state1.properties.log_templates.copy()

        for temp in state2.properties.log_templates:
            if temp not in state1.properties.log_templates:
                props.append(temp)
        state1.properties = StateProperties(props)

        if state2.is_terminal:
            state1.is_terminal = True

        if state2 is self.start_node:
            self.start_node = state1

        if state1.properties.get_prop_hash() in self.prop_by_hash:
            state1.properties = self.prop_by_hash[
                state1.properties.get_prop_hash()]
        else:
            self.prop_by_hash[
                state1.properties.get_prop_hash()] = state1.properties

        self.edges.change_parent_of_children(
            self.state_indices_by_id[id(state1)],
            self.state_indices_by_id[id(state2)])

        self.edges.change_children_of_parents(
            self.state_indices_by_id[id(state2)],
            self.state_indices_by_id[id(state1)])

        del self.states[self.state_indices_by_id[id(state2)]]
        del self.state_indices_by_id[id(state2)]
        del state2

    def determinize(self, state: State) -> State:
        new_state, changed = self.merge_equivalent_children(state)

        # Get the current state's parent
        parents = self.get_incoming_states(new_state)

        # For each parent
        # TODO: Dont include self in parents in case of self loops
        while len(parents) > 0:
            # Remove non-determinism in the parent
            current, changed = self.merge_equivalent_children(parents.pop())

            # If a change occurred, update the list of parents
            if changed:
                new_state = current
                parents = self.get_incoming_states(current)

        return new_state

    def merge_equivalent_children(self, current: State) -> Tuple[State, bool]:
        """
        Merge all equivalent children, such that the resulting automaton remains deterministic while merging.
        :param current: The state of which we want to merge the children
        """
        merged: bool = False

        # Get all the children of the current node
        # TODO: Refactor duplicate lines
        children = self.get_outgoing_states(current)

        # Get the log templates
        children_templates: List[List[str]] = list(
            map(lambda x: x.properties.log_templates, children))

        # Get a list of duplicate states
        # Two states are duplicates if they have any template in common
        duplicates = []
        for i, x in enumerate(children_templates):
            j = self.__equivalence_index(children_templates, x)
            if i != j:
                duplicates.append((children[i], children[j]))
        has_nondeterminism = len(duplicates) > 0

        # While there are still duplicates left
        while len(duplicates) > 0:

            s1, s2 = duplicates.pop()

            if s1 is current:
                current = s2
            self.merge_states(s2, s1)
            merged = True

            # Update the children and duplicates list
            children = self.get_outgoing_states(current)
            children_templates: List[List[str]] = list(
                map(lambda x: x.properties.log_templates, children))

            # TODO Make this more time efficient ie dont recalculate children but add new ones
            duplicates = []
            for i, x in enumerate(children_templates):
                j = self.__equivalence_index(children_templates, x)
                if i != j:
                    duplicates.append((children[i], children[j]))

        if has_nondeterminism:
            children = self.get_outgoing_states_not_self(current)
            for child in children:
                self.merge_equivalent_children(child)

        return current, merged

    def match_log_template_trace(self, trace: List[str]) -> bool:
        node = self.start_node

        for name in trace:
            if node.is_terminal:
                return False

            outgoing = self.get_outgoing_states(node)
            outgoing = list(filter(lambda x: self.template_matches_state(name, x), outgoing))

            if len(outgoing) > 1:
                raise NonDeterminismException()
            elif len(outgoing) == 0:
                return False
            else:
                node = outgoing[0]
        return True

    def match_trace(self, trace: List[str], syntax_tree: SyntaxTree) -> bool:
        """
        Checks if a given trace matches a path in the graph.
        First, the first line is checked against the start node of the graph.
        If it fits, a successor that matches the next line is picked and the list
        is traversed recursively.

        :param trace: The lines to be matched against the tree.
        :return: If the trace corresponds to a sequence of states in the graph,
                 those states are returned in order. If no match is found, None is returned.
        """
        if len(trace) == 0:
            # If the trace is empty, then it has been fully parsed
            return False

        # Check which state the current line belongs to
        template = syntax_tree.search(trace[0])

        # If no state is found, return false
        if template is None:
            return False

        # Get the root's children
        root_children_matching = list(
            filter(lambda s: self.template_matches_state(template.name, s),
                   self.get_outgoing_states(self.start_node)))

        # If no suitable option, return
        if len(root_children_matching) == 0:
            return False

        if len(root_children_matching) > 1:
            raise NonDeterminismException()

        # Randomly pick first suitable state
        current_state: State = root_children_matching[0]

        if self.template_matches_state(template.name, current_state):
            trace[:] = trace[1:]

            while len(trace) > 0:
                # Find the template of the first line in the syntax tree
                template = syntax_tree.search(trace[0])

                # If no state is found, raise an exception
                if template is None:
                    return False

                # Check if any of the children of current node contain the template in their state
                children: List[State] = self.get_outgoing_states(current_state)
                successor: List[State] = list(
                    filter(lambda next_state: self.template_matches_state(
                        template.name, next_state), children))

                if len(successor) == 0:
                    # If none found, the trace cannot be matched
                    return False
                elif len(successor) > 1:
                    raise NonDeterminismException()
                else:
                    # Pick next node
                    current_state = successor[0]
                    # Remove first trace
                    trace = trace[1:]

            if [state for state in self.get_outgoing_states(current_state) if state.is_terminal]:
                return True
            else:
                return False
        else:
            return False

    def __remove_singular_loops(self) -> None:
        """
        Removes equivalent subsequent loops from the graph.
        For example if graph is:
            0
           /\
          1  3
          1  3
          1  1
          2
          1
        The output will be:
            0
           / \
          ⊂1 ⊂3
          |   |
          2   1
          |
          1
          ⊂ indicates a self-loop.
        """

        # Initialize traversal variables
        assert self.start_node is not None
        current = self.start_node
        been = set()
        stack = [(current, been)]

        # While there are still unreached nodes
        while len(stack) > 0:
            # Get the first node and its neighbours
            current, been = stack.pop()
            outgoing = self.get_outgoing_states_not_self(current)

            # Merge all states directly linked to and equivalent to the current state
            while len(outgoing) > 0:
                out = outgoing.pop()
                if current.is_equivalent(out) and current is not out:
                    self.merge_states(current, out)
                    outgoing = self.get_outgoing_states_not_self(current)

            self.merge_equivalent_children(current)

            # Get all outgoing edges except self loops
            outgoing = self.get_outgoing_states_not_self(current)

            # Mark current state as visited
            been.add(current)

            # Continue traversal with all unvisited neighbouring nodes
            for node in [n for n in outgoing if n not in been]:
                stack.append((node, been.copy()))

    @staticmethod
    def template_matches_state(template: str,
                               state: State) -> bool:
        return template in state.properties.log_templates

    @staticmethod
    def __equivalence_index(target_list: List[List[str]],
                            target_items: List[str]) -> Union[int, None]:
        """
        For a given template, find the first index within a list that has a weakly equivalent template.
        Weak equivalence implies that at least one template is common.
        """
        for i, l in enumerate(target_list):
            for item in target_items:
                if item in l:
                    return i
        return None

    def __str__(self):
        return str(self.states)

    def __contains__(self, item: State):
        return id(item) in self.state_indices_by_id

    def __len__(self):
        return len(self.states)
