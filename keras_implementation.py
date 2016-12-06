
# coding: utf-8

# In[123]:

import cv2, numpy as np
import time
import theano
import os
from collections import OrderedDict, defaultdict
import six.moves.cPickle as pkl
from keras.models import Sequential
from keras.layers import Dense, Dropout, Activation, Flatten, LSTM
from keras.layers import Embedding
from keras.layers import Convolution2D, MaxPooling2D, ZeroPadding2D 
from keras.optimizers import SGD
from keras.layers import GRU, TimeDistributed, RepeatVector, Merge, TimeDistributedDense
import h5py
import json
from collections import Counter
import matplotlib.pyplot as plt
import skimage.transform


# In[3]:

MEAN_VALUES = np.array([104, 117, 123]).reshape((3,1,1))
SEQUENCE_LENGTH = 32
MAX_SENTENCE_LENGTH = SEQUENCE_LENGTH - 3 # 1 for image, 1 for start token, 1 for end token
BATCH_SIZE = 20
CNN_FEATURE_SIZE = 1000
EMBEDDING_SIZE = 256


# In[100]:

def word_processing(dataset):
    allwords = Counter()
    for item in dataset:
        for sentence in item['sentences']:
            allwords.update(sentence['tokens'])
            
    vocab = [k for k, v in allwords.items() if v >= 5]
    vocab.insert(0, '#START#')
    vocab.append('#UNK#')
    vocab.append('#END#')

    word_to_index = {w: i for i, w in enumerate(vocab)}
    index_to_word = {i: w for i, w in enumerate(vocab)}
    return vocab, word_to_index, index_to_word

def import_flickr8kdataset():
    dataset = json.load(open('captions/dataset_flickr8k.json'))['images']
    #reduced length to a 300 for testing
    val_set = list(filter(lambda x: x['split'] == 'val', dataset))
    train_set = list(filter(lambda x: x['split'] == 'train', dataset))
    test_set = list(filter(lambda x: x['split'] == 'test', dataset))
    return train_set[:800]+val_set[:200]


# In[101]:

def floatX(arr):
    return np.asarray(arr, dtype=theano.config.floatX)

#Prep Image uses an skimage transform
def prep_image(im):
    if len(im.shape) == 2:
        im = im[:, :, np.newaxis]
        im = np.repeat(im, 3, axis=2)
    # Resize so smallest dim = 224, preserving aspect ratio
    h, w, _ = im.shape
    if h < w:
        im = skimage.transform.resize(im, (224, w*224/h), preserve_range=True)
    else:
        im = skimage.transform.resize(im, (h*224/w, 224), preserve_range=True)

    # Central crop to 224x224
    h, w, _ = im.shape
    im = im[h//2-112:h//2+112, w//2-112:w//2+112]
    
    rawim = np.copy(im).astype('uint8')
    
    # Shuffle axes to c01
    im = np.swapaxes(np.swapaxes(im, 1, 2), 0, 1)
    
    # Convert to BGR
    im = im[::-1, :, :]

    im = im - MEAN_VALUES
    return rawim, floatX(im[np.newaxis])


# In[102]:

def VGG_16(weights_path=None):
    model = Sequential()
    model.add(ZeroPadding2D((1,1),input_shape=(3,224,224)))
    model.add(Convolution2D(64, 3, 3, activation='relu'))
    model.add(ZeroPadding2D((1,1)))
    model.add(Convolution2D(64, 3, 3, activation='relu'))
    model.add(MaxPooling2D((2,2), strides=(2,2)))

    model.add(ZeroPadding2D((1,1)))
    model.add(Convolution2D(128, 3, 3, activation='relu'))
    model.add(ZeroPadding2D((1,1)))
    model.add(Convolution2D(128, 3, 3, activation='relu'))
    model.add(MaxPooling2D((2,2), strides=(2,2)))

    model.add(ZeroPadding2D((1,1)))
    model.add(Convolution2D(256, 3, 3, activation='relu'))
    model.add(ZeroPadding2D((1,1)))
    model.add(Convolution2D(256, 3, 3, activation='relu'))
    model.add(ZeroPadding2D((1,1)))
    model.add(Convolution2D(256, 3, 3, activation='relu'))
    model.add(MaxPooling2D((2,2), strides=(2,2)))

    model.add(ZeroPadding2D((1,1)))
    model.add(Convolution2D(512, 3, 3, activation='relu'))
    model.add(ZeroPadding2D((1,1)))
    model.add(Convolution2D(512, 3, 3, activation='relu'))
    model.add(ZeroPadding2D((1,1)))
    model.add(Convolution2D(512, 3, 3, activation='relu'))
    model.add(MaxPooling2D((2,2), strides=(2,2)))

    model.add(ZeroPadding2D((1,1)))
    model.add(Convolution2D(512, 3, 3, activation='relu'))
    model.add(ZeroPadding2D((1,1)))
    model.add(Convolution2D(512, 3, 3, activation='relu'))
    model.add(ZeroPadding2D((1,1)))
    model.add(Convolution2D(512, 3, 3, activation='relu'))
    model.add(MaxPooling2D((2,2), strides=(2,2)))

    model.add(Flatten())
    model.add(Dense(4096, activation='relu'))
    model.add(Dropout(0.5))
    model.add(Dense(4096, activation='relu'))
    model.add(Dropout(0.5))
    model.add(Dense(1000, activation='softmax'))

    if weights_path:
        model.load_weights(weights_path)

    return model


# In[125]:

def language_model():
    model = Sequential()
    print('Adding Embedding')
    model.add(Embedding(VOCAB_COUNT, EMBEDDING_SIZE, input_length=SEQUENCE_LENGTH-1))
    print('Adding LSTM')
    model.add(LSTM(CNN_FEATURE_SIZE, return_sequences=True))
    print('Adding TimeDistributed Dense')
    model.add(TimeDistributed(Dense(CNN_FEATURE_SIZE)))
    return model


# In[159]:

dataset = import_flickr8kdataset()
# Currently testing it out
dataset = [i for i in dataset[:100]]
vocab,word_to_index, index_to_word = word_processing(dataset)


# In[131]:

def chunks(l, n):
    for i in xrange(0, len(l), n):
        yield l[i:i + n]

def process_images(dataset, coco=False, d_set="Flicker8k_Dataset"):
    ind_process = 1
    total = len(dataset)
    cnn_input = floatX(np.zeros((len(dataset), 3, 224, 224)))
    rawim_input = []
    sentences_tokens = []
    for i, image in enumerate(dataset):
        print "ind_process %s total %s" %(str(ind_process),str(total))
        ind_process+=1
        if coco:
            fn = './coco/{}/{}'.format(image['filepath'], image['filename'])
        else:
            fn = d_set+'/{}'.format(image['filename'])
        try:
            im = plt.imread(fn)
            rawim, cnn_input[i] = prep_image(im)
            sentences_tokens.append(image['sentences'][0]['tokens'])
            rawim_input.append(rawim)
        except IOError:
            continue
    return rawim_input, cnn_input, sentences_tokens


# In[132]:

rawim_array, cnnim_array, sentences_tokens = process_images(dataset, coco=False, d_set="Flicker8k_Dataset")
get_ipython().magic(u'matplotlib inline')


# In[154]:

def gen_image_partial_captions(images, captions, word_to_index, vocab_count):
    a_images = []
    a_captions = []
    next_words = []
    #vocab_size = len(vocab)
    for ind, image in enumerate(images):
        sentence = captions[ind]
        partial_caption_ar = np.zeros(SEQUENCE_LENGTH-1, dtype=np.int)
        
        words = ['#START#'] + sentence + ['#END#']
        assert len(words)<SEQUENCE_LENGTH
        for i in range(len(words) - 1):
            pc_copy = partial_caption_ar.copy()
            if words[i] in word_to_index:
                pc_copy[i] = word_to_index[words[i]]
            else:
                pc_copy[i] = word_to_index["#UNK#"]
            a_images.append(image)
            a_captions.append(pc_copy)
            #Generate next word output vector
            next_word = words[i + 1]
            if next_word in word_to_index:
                next_word_index = word_to_index[next_word]
            else:
                next_word_index = word_to_index["#UNK#"]
            next_word_ar = np.zeros(vocab_count, dtype=np.int)
            next_word_ar[next_word_index] = 1
            next_words.append(next_word_ar)
    v_i = np.array(a_images)
    v_c = np.array(a_captions)
    v_nw = np.array(next_words)
    return v_i, v_c, v_nw 


# In[155]:

vocab_count = len(word_to_index)
print cnnim_array.shape
v_i, v_c, v_nw = gen_image_partial_captions(cnnim_array, sentences_tokens, word_to_index, vocab_count)


# In[156]:

VOCAB_COUNT = len(word_to_index)


# In[157]:

def build_model(weights_path):
    image_model = VGG_16(weights_path)
    image_model.add(RepeatVector(SEQUENCE_LENGTH-1))
    print('Built Image Model')
    print('Building Language Model')
    lang_model = language_model()
    model = Sequential()
    model.add(Merge([image_model, lang_model], mode='concat',  concat_axis=-1))
    model.add(LSTM(EMBEDDING_SIZE, return_sequences=False))
    #print(vocab_size)
    model.add(Dense(VOCAB_COUNT, activation='softmax'))

    #print(model.summary())
    return model


# In[158]:

model=build_model('weights/vgg16_weights.h5')
print('Built model.')
print('Compiling Now')
model.compile(optimizer='adam', loss='categorical_crossentropy', metrics=['accuracy'])
print('Fitting Now')
model.fit([v_i, v_c], v_nw, batch_size=3, nb_epoch=100)
