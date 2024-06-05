# Most of the code here has been modified from:
# https://github.com/bigscience-workshop/data-preparation
# --------------------------------------------------------

from ..base_op import OPERATORS, Mapper, catch_exception_mapper_process_single
from ..common.special_characters import VARIOUS_WHITESPACES


@OPERATORS.register_module('whitespace_normalization_mapper')
class WhitespaceNormalizationMapper(Mapper):
    """
    Mapper to normalize different kinds of whitespaces to whitespace ' ' (0x20)
    in text samples.

    Different kinds of whitespaces can be found here:
    https://en.wikipedia.org/wiki/Whitespace_character
    """

    def __init__(self, *args, **kwargs):
        """
        Initialization method.

        :param args: extra args
        :param kwargs: extra args
        """
        super().__init__(*args, **kwargs)

    @catch_exception_mapper_process_single
    def process(
            self,
            sample):  # remove whitespaces before and after the main content
        text = sample[self.text_key].strip()

        # replace all kinds of whitespaces with ' '
        sample[self.text_key] = ''.join([
            char if char not in VARIOUS_WHITESPACES else ' ' for char in text
        ])

        return sample
