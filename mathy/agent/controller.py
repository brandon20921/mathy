import collections
import math
import os
import random
import sys
import time
from itertools import zip_longest
from multiprocessing import cpu_count
from pathlib import Path

import numpy
import tensorflow as tf
from colr import color

from ..agent.features import (
    FEATURE_BWD_VECTORS,
    FEATURE_FOCUS_INDEX,
    FEATURE_FWD_VECTORS,
    FEATURE_LAST_BWD_VECTORS,
    FEATURE_LAST_FWD_VECTORS,
    FEATURE_MOVE_COUNTER,
    FEATURE_MOVES_REMAINING,
    FEATURE_NODE_COUNT,
    FEATURE_PROBLEM_TYPE,
)
from ..agent.predictor import MathPredictor
from ..core.expressions import MathTypeKeysMax
from ..environment_state import MathEnvironmentState
from .dataset import make_training_input_fn
from .model import math_estimator
from .train_hooks import EpochTrainerHook


class MathModel:
    def __init__(
        self,
        action_size,
        root_dir,
        all_memory=False,
        dev_mode=False,
        init_model_dir=None,
        init_model_overwrite=False,
        # long_term_size=32768,
        long_term_size=2048,
        is_eval_model=False,
        # Karpathy once tweeted this was the best learning rate for Adam optimizer "hands down"
        learning_rate=3e-4,
        # https://arxiv.org/pdf/1801.05134.pdf uses 0.1
        dropout=0.1,
        epochs=10,
        batch_size=512,
        log_frequency=250,
        use_gpu=False,
        random_seed=1337,
    ):
        self.random_seed = random_seed
        self.is_eval_model = is_eval_model
        self.init_model_overwrite = init_model_overwrite
        self.long_term_size = long_term_size
        self.root_dir = root_dir
        self.learning_rate = learning_rate
        self.dropout = dropout
        self.epochs = epochs
        self.batch_size = batch_size
        self.use_gpu = use_gpu
        self.log_frequency = log_frequency
        if not is_eval_model:
            self.model_dir = os.path.join(self.root_dir, "train")
        else:
            self.model_dir = os.path.join(self.root_dir, "eval")
        self.init_model_dir = init_model_dir
        if (
            self.init_model_dir is not None
            and tf.train.latest_checkpoint(self.model_dir) is not None
            and self.init_model_overwrite is not True
        ):
            print(
                "-- skipping trainable variables transfer from model: "
                "(checkpoint exists and overwrite is false)"
            )
            self.init_model_dir = None
        if self.init_model_dir is not None:
            print(
                "-- transferring trainable variables to blank model from: {}".format(
                    self.init_model_dir
                )
            )

        # https://stackoverflow.com/questions/52447908/how-to-explicitly-run-tensor-flow-estimator-on-gpu
        session_config = tf.compat.v1.ConfigProto(
            device_count={"GPU": 1 if use_gpu else 0},
            inter_op_parallelism_threads=10,
            intra_op_parallelism_threads=10,
        )
        session_config.gpu_options.allow_growth = True
        estimator_config = tf.estimator.RunConfig(
            session_config=session_config, tf_random_seed=self.random_seed
        )
        self.action_size = action_size
        self.build_feature_columns()

        #
        # Estimator
        #
        print(
            color(
                "-- init math model in: {}\ninit model dir: {}".format(
                    self.model_dir, self.init_model_dir
                ),
                fore="blue",
                style="bright",
            )
        )
        self.network = tf.estimator.Estimator(
            warm_start_from=self.init_model_dir,
            config=estimator_config,
            model_fn=math_estimator,
            model_dir=self.model_dir,
            params={
                "feature_columns": self.feature_columns,
                "sequence_columns": self.sequence_columns,
                "action_size": self.action_size,
                "learning_rate": self.learning_rate,
                "batch_size": self.batch_size,
                "dropout": self.dropout,
            },
        )
        self._worker = MathPredictor(self.network)

    def build_feature_columns(self):
        """Build out the Tensorflow Feature Columns that define the inputs from Mathy
        into the neural network."""
        self.f_move_count = tf.feature_column.numeric_column(
            key=FEATURE_MOVE_COUNTER, dtype=tf.uint8
        )
        self.f_moves_remaining = tf.feature_column.numeric_column(
            key=FEATURE_MOVES_REMAINING, dtype=tf.uint8
        )
        self.f_focus_index = tf.feature_column.numeric_column(
            key=FEATURE_FOCUS_INDEX, dtype=tf.int8
        )
        self.f_node_count = tf.feature_column.numeric_column(
            key=FEATURE_NODE_COUNT, dtype=tf.uint8
        )
        self.f_problem_type = tf.feature_column.indicator_column(
            tf.feature_column.categorical_column_with_identity(
                key=FEATURE_PROBLEM_TYPE, num_buckets=32
            )
        )
        self.feature_columns = [
            self.f_problem_type,
            self.f_focus_index,
            self.f_node_count,
            self.f_move_count,
            self.f_moves_remaining,
        ]

        vocab_buckets = MathTypeKeysMax + 1

        #
        # Sequence features
        #
        self.feat_bwd_vectors = tf.feature_column.embedding_column(
            tf.feature_column.sequence_categorical_column_with_identity(
                key=FEATURE_BWD_VECTORS, num_buckets=vocab_buckets
            ),
            dimension=32,
        )
        self.feat_fwd_vectors = tf.feature_column.embedding_column(
            tf.feature_column.sequence_categorical_column_with_identity(
                key=FEATURE_FWD_VECTORS, num_buckets=vocab_buckets
            ),
            dimension=32,
        )
        self.feat_last_bwd_vectors = tf.feature_column.embedding_column(
            tf.feature_column.sequence_categorical_column_with_identity(
                key=FEATURE_LAST_BWD_VECTORS, num_buckets=vocab_buckets
            ),
            dimension=32,
        )
        self.feat_last_fwd_vectors = tf.feature_column.embedding_column(
            tf.feature_column.sequence_categorical_column_with_identity(
                key=FEATURE_LAST_FWD_VECTORS, num_buckets=vocab_buckets
            ),
            dimension=32,
        )
        self.sequence_columns = [
            self.feat_fwd_vectors,
            self.feat_bwd_vectors,
            self.feat_last_fwd_vectors,
            self.feat_last_bwd_vectors,
        ]

    def train(
        self, short_term_examples, long_term_examples, train_all=False, sampling_fn=None
    ):
        """examples: list of examples in JSON format"""

        def shuffle_samples(examples_pool, max_items: int):
            """default fn shuffles the remaining examples and returns
            the front {max_items} from the shuffled list"""
            shuffled = examples_pool[:]
            random.shuffle(shuffled)
            return examples_pool[:max_items]

        # Select some observations for training
        if train_all is not True:
            max_examples = self.long_term_size
        else:
            max_examples = len(short_term_examples) + len(long_term_examples)

        # Always sample all of the current episodes observations first
        stm_sample = short_term_examples[:max_examples]
        examples = stm_sample
        ltm_samples = []
        # If there's room left, shuffle the long-term examples and sample
        # the remainder from there.
        if len(examples) < max_examples and len(long_term_examples) > 0:
            remaining_capacity = max_examples - len(examples)
            # Allow user specified sampling logic
            sample_it = shuffle_samples
            if sampling_fn is not None:
                sample_it = sampling_fn
            ltm_samples = sample_it(long_term_examples, remaining_capacity)
            examples = examples + ltm_samples
        # Shuffle all training examples to break temporal dependencies
        random.shuffle(examples)
        print(
            "[stm] sampled {} observations from recent experience".format(
                len(stm_sample)
            )
        )
        print(
            "[training] {} epochs with {} examples, {} learning rate, and {} dropout...".format(
                self.epochs, len(examples), self.learning_rate, self.dropout
            )
        )
        max_steps = len(examples) * self.epochs
        self.network.train(
            hooks=[EpochTrainerHook(self.epochs, len(examples), self.batch_size)],
            steps=max_steps,
            input_fn=make_training_input_fn(examples, self.batch_size),
        )
        return True

    def predict(self, env_state: MathEnvironmentState):
        """Predict a policy/value for a given input state.
        
        Returns: a tuple of (policy, value) for the input as predicted 
        by the neural network """

        input_features = env_state.to_input_features(return_batch=True)
        # start = time.time()
        prediction = self._worker.predict(input_features)
        # print("predict : {0:03f}".format(time.time() - start))
        # print("distribution is : {}".format(prediction[("policy", "predictions")]))
        return (
            prediction[("policy", "predictions")],
            prediction[("value", "predictions")][0],
        )

    def start(self):
        """Start the cached inference worker"""
        self._worker.start()

    def stop(self):
        """Stop the cached inference worker"""
        self._worker.stop()
