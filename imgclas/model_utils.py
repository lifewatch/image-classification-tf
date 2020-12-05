"""
Miscellanous functions to handle models.

Date: September 2018
Author: Ignacio Heredia
Email: iheredia@ifca.unican.es
Github: ignacioheredia
"""

import os
import json

from tensorflow.keras import applications
from tensorflow.keras import regularizers, callbacks
from tensorflow.keras import backend as K
from tensorflow.keras.models import load_model, Model
from tensorflow.python.saved_model import builder as saved_model_builder
from tensorflow.python.saved_model.signature_def_utils import predict_signature_def
from tensorflow.python.saved_model import tag_constants
from tensorflow.keras.layers import Dense, GlobalAveragePooling2D, Flatten, Activation, BatchNormalization, Dropout

import numpy as np



model_modes = {'DenseNet121': 'torch', 'DenseNet169': 'torch', 'DenseNet201': 'torch',
               'InceptionResNetV2': 'tf', 'InceptionV3': 'tf', 'MobileNet': 'tf',
               'NASNetLarge': 'tf', 'NASNetMobile': 'tf', 'Xception': 'tf',
               'ResNet50': 'caffe', 'VGG16': 'caffe', 'VGG19': 'caffe'}


def create_model(CONF, classification=False, output_layers=512):
    """
    Parameters
    ----------
    CONF : dict
        Contains relevant configuration parameters of the model
    """
    architecture = getattr(applications, CONF['model']['modelname'])

    # create the base pre-trained model
    img_width, img_height = CONF['model']['image_size'], CONF['model']['image_size']
    base_model = architecture(weights='imagenet', include_top=False, input_shape = (img_width, img_height, 3))

    # Add custom layers at the top to adapt it to our problem
    x = base_model.output

    if classification:
        x = GlobalAveragePooling2D()(x)
        # x = Flatten()(x) #might work better on large dataset than GlobalAveragePooling https://github.com/keras-team/keras/issues/8470
        x = Dense(1024, activation='relu')(x)
        x = Dense(CONF['model']['num_classes'], activation='softmax')(x)
    else:
        x = Flatten()(x)
        x = Dense(1024)(x)
        x = Activation("relu")(x)
        x = BatchNormalization(axis=-1)(x)
        x = Dense(output_layers)(x)
        x = Activation("relu")(x)
        x = BatchNormalization(axis=-1)(x)
        x = Dropout(0.5)(x)

    # Full model
    model = Model(inputs=base_model.input, outputs=x)

    # Add L2 reguralization for all the layers in the whole model
    if CONF['training']['l2_reg']:
        for layer in model.layers:
            layer.kernel_regularizer = regularizers.l2(CONF['training']['l2_reg'])

    return model, base_model


def save_to_pb(keras_model, export_path):
    """
    Save keras model to protobuf for Tensorflow Serving.
    Source: https://medium.com/@johnsondsouza23/export-keras-model-to-protobuf-for-tensorflow-serving-101ad6c65142

    Parameters
    ----------
    keras_model: Keras model instance
    export_path: str
    """

    # Set the learning phase to Test since the model is already trained.
    K.set_learning_phase(0)

    # Build the Protocol Buffer SavedModel at 'export_path'
    builder = saved_model_builder.SavedModelBuilder(export_path)

    # Create prediction signature to be used by TensorFlow Serving Predict API
    signature = predict_signature_def(inputs={"images": keras_model.input},
                                      outputs={"scores": keras_model.output})

    with K.get_session() as sess:
        # Save the meta graph and the variables
        builder.add_meta_graph_and_variables(sess=sess, tags=[tag_constants.SERVING],
                                             signature_def_map={"predict": signature})

    builder.save()


def export_h5_to_pb(path_to_h5, export_path):
    """
    Transform Keras model to protobuf

    Parameters
    ----------
    path_to_h5
    export_path
    """
    model = load_model(path_to_h5)
    save_to_pb(model, export_path)


def save_conf(conf):
    """
    Save CONF to a txt file to ease the reading and to a json file to ease the parsing.

    Parameters
    ----------
    conf : 1-level nested dict
    """
    from imgclas import paths
    save_dir = paths.get_conf_dir()

    # Save dict as json file
    with open(os.path.join(save_dir, 'conf.json'), 'w') as outfile:
        json.dump(conf, outfile, sort_keys=True, indent=4)

    # Save dict as txt file for easier redability
    txt_file = open(os.path.join(save_dir, 'conf.txt'), 'w')
    txt_file.write("{:<25}{:<30}{:<30} \n".format('group', 'key', 'value'))
    txt_file.write('=' * 75 + '\n')
    for key, val in sorted(conf.items()):
        for g_key, g_val in sorted(val.items()):
            txt_file.write("{:<25}{:<30}{:<15} \n".format(key, g_key, str(g_val)))
        txt_file.write('-' * 75 + '\n')
    txt_file.close()


class LR_scheduler(callbacks.LearningRateScheduler):
    """
    Custom callback to decay the learning rate. Schedule follows a 'step' decay.

    Reference
    ---------
    https://github.com/keras-team/keras/issues/898#issuecomment-285995644
    """
    def __init__(self, lr_decay=0.1, epoch_milestones=[]):
        self.lr_decay = lr_decay
        self.epoch_milestones = epoch_milestones
        super().__init__(schedule=self.schedule)

    def schedule(self, epoch):
        current_lr = K.eval(self.model.optimizer.lr)
        if epoch in self.epoch_milestones:
            new_lr = current_lr * self.lr_decay
            print('Decaying the learning rate to {}'.format(new_lr))
        else:
            new_lr = current_lr
        return new_lr


class LRHistory(callbacks.Callback):
    """
    Custom callback to save the learning rate history

    Reference
    ---------
    https://stackoverflow.com/questions/49127214/keras-how-to-output-learning-rate-onto-tensorboard
    """
    def __init__(self):  # add other arguments to __init__ if needed
        super().__init__()

    def on_epoch_end(self, epoch, logs=None):
        logs.update({'lr': K.eval(self.model.optimizer.lr).astype(np.float64)})
        super().on_epoch_end(epoch, logs)


def get_callbacks(CONF, use_lr_decay=True):
    """
    Get a callback list to feed fit_generator.
    #TODO Use_remote callback needs proper configuration
    #TODO Add ReduceLROnPlateau callback?

    Parameters
    ----------
    CONF: dict

    Returns
    -------
    List of callbacks
    """

    calls = []

    # Add mandatory callbacks
    calls.append(callbacks.TerminateOnNaN())
    calls.append(LRHistory())

    # Add optional callbacks
    if use_lr_decay:
        milestones = np.array(CONF['training']['lr_step_schedule']) * CONF['training']['epochs']
        milestones = milestones.astype(np.int)
        calls.append(LR_scheduler(lr_decay=CONF['training']['lr_step_decay'],
                                  epoch_milestones=milestones.tolist()))

    # if CONF['monitor']['use_tensorboard']:
    #     calls.append(callbacks.TensorBoard(log_dir=paths.get_logs_dir(), write_graph=False))
    #
    #     # # Let the user launch Tensorboard
    #     # print('Monitor your training in Tensorboard by executing the following comand on your console:')
    #     # print('    tensorboard --logdir={}'.format(paths.get_logs_dir()))
    #     # Run Tensorboard  on a separate Thread/Process on behalf of the user
    #     port = os.getenv('monitorPORT', 6006)
    #     port = int(port) if len(str(port)) >= 4 else 6006
    #     subprocess.run(['fuser', '-k', '{}/tcp'.format(port)]) # kill any previous process in that port
    #     p = Process(target=launch_tensorboard, args=(port,), daemon=True)
    #     p.start()

    if CONF['monitor']['use_remote']:
        calls.append(callbacks.RemoteMonitor())

    if CONF['training']['use_validation'] and CONF['training']['use_early_stopping']:
        calls.append(callbacks.EarlyStopping(patience=int(0.1 * CONF['training']['epochs'])))

    # if CONF['training']['ckpt_freq'] is not None:
    #     calls.append(callbacks.ModelCheckpoint(
    #         os.path.join(paths.get_checkpoints_dir(), 'epoch-{epoch:02d}.hdf5'),
    #         verbose=1,
    #         period=max(1, int(CONF['training']['ckpt_freq'] * CONF['training']['epochs']))))

    if not calls:
        calls = None

    return calls
