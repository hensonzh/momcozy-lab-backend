"""Opt-in real media check using a local LiveKit server and synthetic frames only."""

import asyncio
import json
import math
import os
from array import array
from pathlib import Path

import pytest

from app.infrastructure.video.provider import LiveKitVideoProvider
from app.modules.appointments.schemas import VersionWrite
from app.modules.care.models import CareEpisode
from app.modules.consultations.room_schemas import EndWrite, LocationWrite, PresenceWrite
from app.workers.care_video import process_next
from test_care_booking import postgres
from test_care_rooms import room_case

CONFIG = os.getenv('MOMCOZY_TEST_LIVEKIT_CONFIG', '')


@postgres
@pytest.mark.skipif(not CONFIG, reason='Requires opt-in local LiveKit configuration and the livekit RTC test client.')
def test_real_livekit_media_in_both_directions_and_durable_expert_room_closure():
    from livekit import rtc

    async def run():
        config = json.loads(Path(CONFIG).read_text())
        video = LiveKitVideoProvider(url=config['server_url'], api_key=config['api_key'], api_secret=config['api_secret'])
        async with room_case() as (sessions, service, at, appointment, mom, expert, _, _):
            def rooms(session):
                return service(session, media=video)
            async with sessions.begin() as session:
                await rooms(session).check_location(mom, appointment.id, LocationWrite(region='CA'), 'location')
                await rooms(session).prepare(mom, appointment.id, 'prepare')
            assert await process_next(sessions, video, now=lambda: at[0])
            joins = []
            async with sessions.begin() as session:
                for user in [mom, expert]:
                    joins.append(await rooms(session).join(user, appointment.id, str(user.user_id), 'join'))

            clients = [rtc.Room(), rtc.Room()]
            closed = [asyncio.Event(), asyncio.Event()]
            subscribed = [asyncio.Event(), asyncio.Event()]
            streams = [{}, {}]
            sources = []
            sender_tasks = []
            stop_sending = asyncio.Event()
            loop = asyncio.get_running_loop()
            for index, client in enumerate(clients):
                def received(track, publication, participant, *, index=index):
                    if track.kind == rtc.TrackKind.KIND_VIDEO:
                        streams[index]['video'] = rtc.VideoStream(track)
                    else:
                        streams[index]['audio'] = rtc.AudioStream(track)
                    if len(streams[index]) == 2:
                        subscribed[index].set()
                client.on('track_subscribed', received)
                client.on('disconnected', lambda reason, index=index: closed[index].set())
            try:
                for client, joined in zip(clients, joins, strict=True):
                    await client.connect(joined.credentials.server_url, joined.credentials.token)
                async with sessions.begin() as session:
                    for user, joined in zip([mom, expert], joins, strict=True):
                        await rooms(session).presence(user, appointment.id, PresenceWrite(connection_id=joined.connection_id, presence='joined'), 'media-connected')
                    started = await rooms(session).start(expert, appointment.id, VersionWrite(expected_version=1), 'start')
                    assert started.consultation.status == 'in_progress'
                for index, client in enumerate(clients):
                    camera = rtc.VideoSource(160, 120)
                    microphone = rtc.AudioSource(48000, 1)
                    sources.extend([camera, microphone])
                    video_track = rtc.LocalVideoTrack.create_video_track('synthetic-camera', camera)
                    audio_track = rtc.LocalAudioTrack.create_audio_track('synthetic-microphone', microphone)
                    await client.local_participant.publish_track(video_track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
                    await client.local_participant.publish_track(audio_track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
                    async def send(camera=camera, microphone=microphone, index=index):
                        pixels = bytes([30 + index * 60, 100, 180, 255]) * (160 * 120)
                        samples = array('h', [int(3000 * math.sin(2 * math.pi * (440 + index * 440) * value / 48000)) for value in range(480)]).tobytes()
                        while not stop_sending.is_set():
                            camera.capture_frame(rtc.VideoFrame(160, 120, rtc.VideoBufferType.RGBA, pixels))
                            await microphone.capture_frame(rtc.AudioFrame(samples, 48000, 1, 480))
                            await asyncio.sleep(.01)
                    sender_tasks.append(loop.create_task(send()))
                async with asyncio.timeout(25):
                    await asyncio.gather(*(event.wait() for event in subscribed))
                    for received in streams:
                        video_frame = await anext(received['video'])
                        audio_frame = await anext(received['audio'])
                        assert video_frame.frame.width == 160 and video_frame.frame.height == 120
                        assert audio_frame.frame.samples_per_channel > 0
                async with sessions.begin() as session:
                    ended = await rooms(session).end(expert, appointment.id, EndWrite(expected_version=started.consultation.version, reason='completed'), 'completed')
                    assert ended.consultation.room_status == 'closing'
                assert await process_next(sessions, video, now=lambda: at[0])
                async with asyncio.timeout(10):
                    await asyncio.gather(*(event.wait() for event in closed))
                async with sessions.begin() as session:
                    final = await rooms(session).context(mom, appointment.id)
                    assert final.consultation.status == 'note_pending' and final.consultation.room_status == 'closed'
                    assert (await session.get(CareEpisode, appointment.episode_id)).remaining_sessions == 1
            finally:
                stop_sending.set()
                for task in sender_tasks:
                    task.cancel()
                await asyncio.gather(*sender_tasks, return_exceptions=True)
                for received in streams:
                    for stream in received.values():
                        await stream.aclose()
                await asyncio.gather(*(client.disconnect() for client in clients))
                for source in sources:
                    await source.aclose()
    asyncio.run(run())
