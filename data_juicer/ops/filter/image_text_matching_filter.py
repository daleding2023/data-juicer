import numpy as np
from jsonargparse.typing import ClosedUnitInterval

from data_juicer.utils.availability_utils import AvailabilityChecking
from data_juicer.utils.constant import Fields, StatsKeys
from data_juicer.utils.mm_utils import SpecialTokens, load_image
from data_juicer.utils.model_utils import get_model, prepare_model

from ..base_op import OPERATORS, Filter
from ..op_fusion import LOADED_IMAGES

OP_NAME = 'image_text_matching_filter'

with AvailabilityChecking(['torch'], OP_NAME):
    import torch
    import transformers  # noqa: F401

    # avoid hanging when calling blip in multiprocessing
    torch.set_num_threads(1)


@OPERATORS.register_module(OP_NAME)
@LOADED_IMAGES.register_module(OP_NAME)
class ImageTextMatchingFilter(Filter):
    """Filter to keep samples those matching score between image and text
    within a specific range."""

    def __init__(self,
                 hf_blip='Salesforce/blip-itm-base-coco',
                 min_score: ClosedUnitInterval = 0.1,
                 max_score: ClosedUnitInterval = 1.0,
                 any_or_all: str = 'any',
                 reduce_mode: str = 'avg',
                 *args,
                 **kwargs):
        """
        Initialization method.

        :param hf_blip: blip model name on huggingface to compute
            the similarity between image and text.
        :param min_score: The min similarity to keep samples.
        :param max_score: The max similarity to keep samples.
        :param any_or_all: keep this sample with 'any' or 'all' strategy of
            all images. 'any': keep this sample if any images meet the
            condition. 'all': keep this sample only if all images meet the
            condition.
        :param reduce_mode: reduce mode when one text corresponds to
            multiple images in a chunk.
            'avg': Take the average of multiple values
            'max': Take the max of multiple values
            'min': Take the min of multiple values
        :param args: extra args
        :param kwargs: extra args
        """
        super().__init__(*args, **kwargs)
        self.min_score = min_score
        self.max_score = max_score
        if reduce_mode not in ['avg', 'max', 'min']:
            raise ValueError(f'Reduce mode [{reduce_mode}] is not supported. '
                             f'Can only be one of ["avg", "max", "min"].')
        if any_or_all not in ['any', 'all']:
            raise ValueError(f'Keep strategy [{any_or_all}] is not supported. '
                             f'Can only be one of ["any", "all"].')
        self.any = (any_or_all == 'any')
        self.model_key = prepare_model(model_type='hf_blip', model_key=hf_blip)
        self.reduce_mode = reduce_mode

    def compute_stats(self, sample, context=False):
        # check if it's computed already
        if StatsKeys.image_text_matching_score in sample[Fields.stats]:
            return sample

        # there is no image in this sample
        if self.image_key not in sample or not sample[self.image_key]:
            sample[Fields.stats][
                StatsKeys.image_text_matching_score] = np.array(
                    [], dtype=np.float64)
            return sample

        # load images
        loaded_image_keys = sample[self.image_key]
        images = {}
        for loaded_image_key in loaded_image_keys:
            if context and loaded_image_key in sample[Fields.context]:
                # load from context
                images[loaded_image_key] = sample[
                    Fields.context][loaded_image_key]
            else:
                if loaded_image_key not in images:
                    # avoid load the same images
                    image = load_image(loaded_image_key)
                    images[loaded_image_key] = image
                    if context:
                        # store the image data into context
                        sample[Fields.context][loaded_image_key] = image

        text = sample[self.text_key]
        special_token_dict = {
            key: value
            for key, value in SpecialTokens.__dict__.items()
            if not key.startswith('__')
        }
        offset = 0

        def remove_special_token(text):
            for value in special_token_dict.values():
                text = text.replace(value, '')
            return text

        matching_scores = []
        model, processor = get_model(self.model_key)

        for chunk in text.split(SpecialTokens.eoc):
            count = chunk.count(SpecialTokens.image)

            # no image or no text
            if count == 0 or len(chunk) == 0:
                continue
            else:
                text_chunk = remove_special_token(chunk)
                image_chunk = [
                    images[image_key]
                    for image_key in loaded_image_keys[offset:offset + count]
                ]

                inputs = processor(text=text_chunk,
                                   images=image_chunk,
                                   return_tensors='pt',
                                   truncation=True,
                                   max_length=model.config.text_config.
                                   max_position_embeddings,
                                   padding=True)

                outputs = model(**inputs)
                itm_scores = outputs.itm_score.detach().cpu().softmax(
                    dim=-1)[:, 1]

                if self.reduce_mode == 'avg':
                    chunk_itm_score = itm_scores.mean()
                elif self.reduce_mode == 'max':
                    chunk_itm_score = itm_scores.max()
                else:
                    chunk_itm_score = itm_scores.min()

                matching_scores.append(float(chunk_itm_score))
            offset += count
        sample[Fields.stats][
            StatsKeys.image_text_matching_score] = matching_scores

        return sample

    def process(self, sample):
        itm_scores = sample[Fields.stats][StatsKeys.image_text_matching_score]
        if len(itm_scores) <= 0:
            return True

        keep_bools = np.array([
            self.min_score <= itm_score <= self.max_score
            for itm_score in itm_scores
        ])

        # different strategies
        if self.any:

            return keep_bools.any()
        else:
            return keep_bools.all()
