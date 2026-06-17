FROM public.ecr.aws/lambda/python:3.11

RUN yum install -y google-noto-sans-cjk-fonts tar xz \
    && yum clean all \
    && rm -rf /var/cache/yum

COPY fonts/ /usr/share/fonts/truetype/vendor/

RUN curl -fsSL \
    -A "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
    --retry 3 --retry-delay 2 -L \
    https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz \
    | tar -xJ --strip-components=1 -C /usr/local/bin \
    --wildcards 'ffmpeg-*-amd64-static/ffmpeg' 'ffmpeg-*-amd64-static/ffprobe' \
    && chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe \
    && ffmpeg -version && ffprobe -version

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY src/sow_render_worker/ ${LAMBDA_TASK_ROOT}/sow_render_worker/

CMD ["sow_render_worker.lambda_handler.handler"]
