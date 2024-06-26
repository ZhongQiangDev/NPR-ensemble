import numpy as np
from gensim.models import word2vec
import torch
from torch.utils import data
from torch.utils.data import Dataset
from typing import List
from data.vocab import Vocab
from parser.DFG import DFG_python, DFG_java, DFG_ruby, DFG_go, DFG_php, DFG_javascript, DFG_csharp
from parser.utils import (remove_comments_and_docstrings,
                          tree_to_token_index,
                          index_to_code_token,
                          tree_to_variable_index)
from tree_sitter import Language, Parser

dfg_function = {
    'python': DFG_python,
    'java': DFG_java,
    'ruby': DFG_ruby,
    'go': DFG_go,
    'php': DFG_php,
    'javascript': DFG_javascript,
    'c_sharp': DFG_csharp,
}
# load parsers
parsers = {}
for lang in dfg_function:
    LANGUAGE = Language('parser/my-languages.so', lang)
    parser = Parser()
    parser.set_language(LANGUAGE)
    parser = [parser, dfg_function[lang]]
    parsers[lang] = parser


def train_word2vec(x):
    return word2vec.Word2Vec(x, vector_size=300, window=5, min_count=2, sg=1, epochs=5)


class DataProcess:
    def __init__(self, sentences, sen_len, w2v_path):
        self.sentences = sentences
        self.sen_len = sen_len 
        self.w2v_path = w2v_path
        self.index2word = []
        self.word2index = {}
        self.embedding_matrix = []
        # load word2vec.model
        self.embedding = word2vec.Word2Vec.load(self.w2v_path)
        self.embedding_dim = self.embedding.vector_size

    def make_embedding(self):
        for i, word in enumerate(self.embedding.wv.index_to_key):
            print('get word #{}'.format(i + 1), end='\r')
            self.word2index[word] = len(self.word2index)
            self.index2word.append(word)
            self.embedding_matrix.append(self.embedding.wv[word])
        self.embedding_matrix = torch.tensor(self.embedding_matrix)

        self.add_embedding("<PAD>")
        self.add_embedding("<UNK>")
        print("total words: {}".format(len(self.embedding_matrix)))

        return self.embedding_matrix

    def add_embedding(self, word):
        vector = torch.empty(1, self.embedding_dim)
        torch.nn.init.uniform_(vector)
        self.word2index[word] = len(self.word2index)
        self.index2word.append(word)
        self.embedding_matrix = torch.cat([self.embedding_matrix, vector], 0)

    def sentence_word2idx(self):
        sentence_list = []
        for i, sentence in enumerate(self.sentences):
            sentence_index = []
            for word in sentence:
                if word in self.word2index.keys():
                    sentence_index.append(self.word2index[word])
                else:
                    sentence_index.append(self.word2index["<UNK>"])

            sentence_index = self.pad_sequence(sentence_index)
            sentence_list.append(sentence_index)

        return torch.LongTensor(sentence_list)

    def pad_sequence(self, sentence):
        if len(sentence) > self.sen_len:
            sentence = sentence[:self.sen_len]
        else:
            pad_len = self.sen_len - len(sentence)
            for _ in range(pad_len):
                sentence.append(self.word2index["<PAD>"])
        assert len(sentence) == self.sen_len

        return sentence


class NPRDataset(data.Dataset):
    def __init__(self, x, y):
        self.data = x
        self.label = y

    def __getitem__(self, index):
        if self.label is None:
            return self.data[index]

        return self.data[index], self.label[index]

    def __len__(self):
        return len(self.data)


def get_batch_inputs(batch: List[str], vocab: Vocab, processor=None, max_len=None):
    """
    Encode the given batch to input to the model.

    Args:
        batch (list[str]): Batch of sequence,
            each sequence is represented by a string or list of tokens
        vocab (Vocab): Vocab of the batch
        processor (tokenizers.processors.PostProcessor): Optional, post-processor method
        max_len (int): Optional, the maximum length of each sequence

    Returns:
        (torch.LongTensor, torch.LongTensor): Tensor of batch and mask, [B, T]

    """
    # set post processor
    vocab.tokenizer.post_processor = processor
    # set truncation
    if max_len:
        vocab.tokenizer.enable_truncation(max_length=max_len)
    else:
        vocab.tokenizer.no_truncation()
    # encode batch
    inputs, padding_mask = vocab.encode_batch(batch, pad=True, max_length=max_len)
    # to tensor
    inputs = torch.tensor(inputs, dtype=torch.long)
    padding_mask = torch.tensor(padding_mask, dtype=torch.long)

    return inputs, padding_mask


def extract_dataflow(code, parser, lang):
    # remove comments
    try:
        code = remove_comments_and_docstrings(code, lang)
    except:
        pass
        # obtain dataflow
    if lang == "php":
        code = "<?php" + code + "?>"
    try:
        tree = parser[0].parse(bytes(code, 'utf8'))
        root_node = tree.root_node
        tokens_index = tree_to_token_index(root_node)
        code = code.split('\n')
        code_tokens = [index_to_code_token(x, code) for x in tokens_index]
        index_to_code = {}
        for idx, (index, code) in enumerate(zip(tokens_index, code_tokens)):
            index_to_code[index] = (idx, code)
        try:
            DFG, _ = parser[1](root_node, index_to_code, {})
        except:
            DFG = []
        DFG = sorted(DFG, key=lambda x: x[1])
        indexs = set()
        for d in DFG:
            if len(d[-1]) != 0:
                indexs.add(d[1])
            for x in d[-1]:
                indexs.add(x)
        new_DFG = []
        for d in DFG:
            if d[1] in indexs:
                new_DFG.append(d)
        dfg = new_DFG
    except:
        dfg = []
    return code_tokens, dfg


def data_transformer(tokenizer, text, max_length, dfg_max_length):
    code_tokens, dfg = extract_dataflow(text, parsers['java'], 'java')
    code_tokens = [tokenizer.tokenize('@ ' + x)[1:] if idx != 0 else tokenizer.tokenize(x) for idx, x in enumerate(code_tokens)]
    ori2cur_pos = {}
    ori2cur_pos[-1] = (0, 0)
    for i in range(len(code_tokens)):
        ori2cur_pos[i] = (ori2cur_pos[i - 1][1], ori2cur_pos[i - 1][1] + len(code_tokens[i]))
    code_tokens = [y for x in code_tokens for y in x]
    # code
    code_tokens = code_tokens[:max_length - 2 - min(len(dfg), dfg_max_length)]
    code_tokens = [tokenizer.cls_token] + code_tokens + [tokenizer.sep_token]
    code_ids = tokenizer.convert_tokens_to_ids(code_tokens)
    position_idx = [i + tokenizer.pad_token_id + 1 for i in range(len(code_tokens))]
    # dfg
    dfg = dfg[:max_length - len(code_tokens)]
    code_tokens += [x[0] for x in dfg]
    code_ids += [tokenizer.unk_token_id for x in dfg]
    position_idx += [0 for x in dfg]
    # padding
    padding_length = max_length - len(code_ids)
    code_ids += [tokenizer.pad_token_id] * padding_length
    position_idx += [tokenizer.pad_token_id] * padding_length
    # reindex
    reverse_index = {}
    for idx, x in enumerate(dfg):
        reverse_index[x[1]] = idx
    for idx, x in enumerate(dfg):
        dfg[idx] = x[:-1] + ([reverse_index[i] for i in x[-1] if i in reverse_index],)
    dfg_to_dfg = [x[-1] for x in dfg]
    dfg_to_code = [ori2cur_pos[x[1]] for x in dfg]
    length = len([tokenizer.cls_token])
    dfg_to_code = [(x[0] + length, x[1] + length) for x in dfg_to_code]
    # attn_mask
    attn_mask = np.zeros((max_length, max_length), dtype=np.bool)
    # calculate begin index of node and max length of input
    node_index = sum([i > 1 for i in position_idx])
    max_length = sum([i != 1 for i in position_idx])
    # sequence can attend to sequence
    attn_mask[:node_index, :node_index] = True
    # special tokens attend to all tokens
    for idx, i in enumerate(code_ids):
        if i in [0, 2]:
            attn_mask[idx, :max_length] = True
    # nodes attend to code tokens that are identified from
    for idx, (a, b) in enumerate(dfg_to_code):
        if a < node_index and b < node_index:
            attn_mask[idx + node_index, a:b] = True
            attn_mask[a:b, idx + node_index] = True
    # nodes attend to adjacent nodes
    for idx, nodes in enumerate(dfg_to_dfg):
        for a in nodes:
            if a + node_index < len(position_idx):
                attn_mask[idx + node_index, a + node_index] = True

    return code_ids, position_idx, attn_mask


class Sample:
    def __init__(self, text_id, text_pos, text_mask):
        self.code_ids = text_id
        self.position_idx = text_pos
        self.attn_mask = text_mask


class TextDataset(Dataset):
    def __init__(self, x, y):
        self.examples = x
        self.labels = y

    def __len__(self):
        return len(self.examples)

    def __getitem__(self, item):
        return (torch.tensor(self.examples[item].code_ids),
                torch.tensor(self.examples[item].position_idx),
                torch.tensor(self.examples[item].attn_mask),
                torch.tensor(self.labels[item]))
