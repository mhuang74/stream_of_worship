FROM public.ecr.aws/lambda/python:3.11

RUN yum install -y google-noto-sans-cjk-fonts tar xz \
    && yum clean all \
    && rm -rf /var/cache/yum

COPY fonts/ /usr/share/fonts/truetype/vendor/

RUN pip install --no-cache-dir boto3

ARG FFMPEG_VERSION=7.0.2
ARG R2_BUCKET
ARG R2_ENDPOINT_URL
ARG R2_ACCESS_KEY_ID
ARG R2_SECRET_ACCESS_KEY
ARG REQUIRE_R2_FFMPEG=false

COPY scripts/download-ffmpeg.sh /tmp/download-ffmpeg.sh
RUN chmod +x /tmp/download-ffmpeg.sh \
    && /tmp/download-ffmpeg.sh "${FFMPEG_VERSION}" "${R2_BUCKET}" "${R2_ENDPOINT_URL}" "${R2_ACCESS_KEY_ID}" "${R2_SECRET_ACCESS_KEY}" "${REQUIRE_R2_FFMPEG}" \
    && rm -f /tmp/download-ffmpeg.sh

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY src/sow_render_worker/ ${LAMBDA_TASK_ROOT}/sow_render_worker/

CMD ["sow_render_worker.lambda_handler.handler"]
