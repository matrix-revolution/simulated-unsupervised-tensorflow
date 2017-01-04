from tqdm import trange
import tensorflow as tf
from tensorflow.contrib.framework.python.ops import arg_scope

from model import Model
from buffer import Buffer
import data.gaze_data as gaze_data
import data.hand_data as hand_data

class Trainer(object):
  def __init__(self, config, rng):
    self.config = config
    self.task = config.task
    self.model_dir = config.model_dir
    self.gpu_memory_fraction = config.gpu_memory_fraction

    self.log_step = config.log_step
    self.max_step = config.max_step

    self.K_d = config.K_d
    self.K_g = config.K_g
    self.initial_K_d = config.initial_K_d
    self.initial_K_g = config.initial_K_g
    self.checkpoint_secs = config.checkpoint_secs

    DataLoader = {
        'gaze': gaze_data.DataLoader,
        'hand': hand_data.DataLoader,
    }[config.data_set]
    self.data_loader = DataLoader(config, rng=rng)

    self.model = Model(config, self.data_loader)
    self.history_buffer = Buffer(config, rng)

    self.summary_ops = {}
    self.summary_placeholders = {}

    self.saver = tf.train.Saver()
    self.summary_writer = tf.summary.FileWriter(self.model_dir)

  def train(self):
    self.model.build_optim()

    sv = tf.train.Supervisor(logdir=self.model_dir,
                             is_chief=True,
                             saver=self.saver,
                             summary_op=None,
                             summary_writer=self.summary_writer,
                             save_summaries_secs=300,
                             save_model_secs=self.checkpoint_secs,
                             global_step=self.model.discrim_step)

    gpu_options = tf.GPUOptions(
        per_process_gpu_memory_fraction=self.gpu_memory_fraction,
        allow_growth=True) # seems to be not working
    sess_config = tf.ConfigProto(allow_soft_placement=True,
                                 gpu_options=gpu_options)

    sess = sv.prepare_or_wait_for_session(config=sess_config)

    print("[*] Training starts...")
    self._summary_writer = None

    def train_refiner():
      feed_dict = {
        self.model.synthetic_batch_size: self.data_loader.batch_size,
      }
      res = self.model.train_refiner(
          sess, feed_dict, self._summary_writer, with_output=True)
      self.history_buffer.push(res['output'])
      self._summary_writer = self._get_summary_writer(res)

    def train_discrim():
      feed_dict = {
        self.model.synthetic_batch_size: self.data_loader.batch_size/2,
        self.model.R_x_history: self.history_buffer.sample(),
        self.model.y: self.data_loader.next(),
      }
      res = self.model.train_discrim(
          sess, feed_dict, self._summary_writer, with_history=True, with_output=False)
      self._summary_writer = self._get_summary_writer(res)
      # self.model.D_R_x_logits.eval(feed_dict, session=sess)[:10,0,0,:]
      # self.model.D_y_logits.eval(feed_dict, session=sess)[:10,0,0,:]

    for k in trange(self.initial_K_g, desc="Train refiner"):
      train_refiner()

    for k in trange(self.initial_K_d, desc="Train discrim"):
      train_discrim()

    for step in trange(self.max_step, desc="Train both"):
      for k in xrange(self.K_g):
        train_refiner()

      for k in xrange(self.K_d):
        train_discrim()

  def test(self):
    pass

  def _inject_summary(self, sess, tag_dict, step):
    feed_dict = {
        self.summary_placeholders[tag]: \
            value for tag, value in tag_dict.items()
    }
    summaries = sess.run(
        [self.summary_ops[tag] for tag in tag_dict.keys()], feed_dict)

    for summary in summaries:
      self.summary_writer.add_summary(summary, step)

  def _create_summary_op(self, tags):
    if type(tags) != list:
      tags = [tags]

    for tag in tags:
      self.summary_placeholders[tag] = \
          tf.placeholder('float32', None, name=tag.replace(' ', '_'))
      self.summary_ops[tag] = \
          tf.summary.scalar(tag, self.summary_placeholders[tag])

  def _get_summary_writer(self, result):
    if result['step'] % self.log_step == 0:
      return self.summary_writer
    else:
      return None
