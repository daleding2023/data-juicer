import copy
import re

import numpy as np

from data_juicer.utils.file_utils import (add_suffix_to_filename,
                                          transfer_filename)
from data_juicer.utils.mm_utils import (SpecialTokens, cut_video_by_seconds,
                                        get_video_duration, load_video)

from ..base_op import OPERATORS, Mapper
from ..op_fusion import LOADED_VIDEOS


def create_replacer(replacements):

    def replacer(match):
        return replacements.pop(0)

    return replacer


OP_NAME = 'video_split_by_duration_mapper'


@OPERATORS.register_module(OP_NAME)
@LOADED_VIDEOS.register_module(OP_NAME)
class VideoSplitByDurationMapper(Mapper):
    """Mapper to split video by duration.
    """

    def __init__(self,
                 split_duration: float = 10,
                 min_last_split_duration: float = 0,
                 keep_original_sample: bool = True,
                 *args,
                 **kwargs):
        """
        Initialization method.

        :param split_duration: duration of each video split in seconds.
        :param min_last_split_duration: The minimum allowable duration in
            seconds for the last video split. If the duration of the last
            split is less than this value, it will be discarded.
        :param keep_original_sample: whether to keep the original sample. If
            it's set to False, there will be only cut sample in the
            final datasets and the original sample will be removed. It's True
            in default.
        :param args: extra args
        :param kwargs: extra args
        """
        super().__init__(*args, **kwargs)
        self._init_parameters = self.remove_extra_parameters(locals())
        self._batched_op = True
        self.split_duration = split_duration
        self.min_last_split_duration = min_last_split_duration
        self.keep_original_sample = keep_original_sample
        self.extra_args = kwargs

    def split_videos_by_duration(self, video_key, container):
        video_duration = get_video_duration(container)
        timestamps = np.arange(0, video_duration, self.split_duration).tolist()
        count = 0
        split_video_keys = []
        for i in range(1, len(timestamps)):
            split_video_key = transfer_filename(video_key, OP_NAME,
                                                **self._init_parameters)
            suffix = '_split-by-duration-' + str(count)
            split_video_key = add_suffix_to_filename(split_video_key, suffix)
            if cut_video_by_seconds(container, split_video_key,
                                    timestamps[i - 1], timestamps[i]):
                split_video_keys.append(split_video_key)
                count += 1

        if video_duration - timestamps[-1] >= self.min_last_split_duration:
            split_video_key = transfer_filename(video_key, OP_NAME,
                                                **self._init_parameters)
            suffix = '_split-by-duration-' + str(count)
            split_video_key = add_suffix_to_filename(split_video_key, suffix)

            if cut_video_by_seconds(container, split_video_key,
                                    timestamps[-1]):
                split_video_keys.append(split_video_key)
        return split_video_keys

    def _process_single_sample(self, sample):
        # there is no video in this sample
        if self.video_key not in sample or sample[
                self.video_key] is None or len(sample[self.video_key]) == 0:
            return []

        # the split results
        split_sample = copy.deepcopy(sample)
        split_sample[self.text_key] = ''

        # load all video(s)
        loaded_video_keys = sample[self.video_key]
        videos = {}
        for loaded_video_key in loaded_video_keys:
            if loaded_video_key not in videos:
                # avoid loading the same videos
                video = load_video(loaded_video_key)
                videos[loaded_video_key] = video

        split_video_keys = []
        offset = 0
        # split each video chunk by chunk
        for chunk in sample[self.text_key].split(SpecialTokens.eoc):
            # skip empty chunks or contents after the last eoc token
            if not chunk.strip():
                continue
            else:
                video_count = chunk.count(SpecialTokens.video)
                place_holders = []
                for video_key in loaded_video_keys[offset:offset +
                                                   video_count]:
                    video = videos[video_key]
                    new_video_keys = self.split_videos_by_duration(
                        video_key, video)
                    video.close()
                    split_video_keys.extend(new_video_keys)
                    place_holders.append(SpecialTokens.video *
                                         len(new_video_keys))

                # insert the generated text according to given mode
                replacer_function = create_replacer(place_holders)
                new_split_text_per_chunk = re.sub(SpecialTokens.video,
                                                  replacer_function, chunk)
                split_sample[
                    self.
                    text_key] += f'{new_split_text_per_chunk}{SpecialTokens.eoc}'  # noqa: E501
                offset += video_count

        split_sample[self.video_key] = split_video_keys
        return [split_sample]

    def process(self, samples):
        # reconstruct samples from "dict of lists" to "list of dicts"
        reconstructed_samples = []
        for i in range(len(samples[self.text_key])):
            reconstructed_samples.append(
                {key: samples[key][i]
                 for key in samples})
        samples_after_split = []
        # do split for each sample within the batch
        for ori_sample in reconstructed_samples:
            if self.keep_original_sample:
                samples_after_split.append(ori_sample)
            generated_samples = self._process_single_sample(ori_sample)
            if len(generated_samples) != 0:
                samples_after_split.extend(generated_samples)
        # reconstruct samples from "list of dicts" to "dict of lists"
        keys = samples_after_split[0].keys()
        res_samples = {}
        for key in keys:
            res_samples[key] = [s[key] for s in samples_after_split]

        return res_samples
