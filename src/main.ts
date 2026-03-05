import {
  type JobContext,
  type JobProcess,
  ServerOptions,
  cli,
  defineAgent,
  inference,
  metrics,
  voice,
} from '@livekit/agents';
import * as livekit from '@livekit/agents-plugin-livekit';
import * as silero from '@livekit/agents-plugin-silero';
import { BackgroundVoiceCancellation } from '@livekit/noise-cancellation-node';
import dotenv from 'dotenv';
import { fileURLToPath } from 'node:url';
import { Agent } from './agent';

dotenv.config({ path: '.env.local' });

export default defineAgent({
  prewarm: async (proc: JobProcess) => {
    proc.userData.vad = await silero.VAD.load();
  },
  entry: async (ctx: JobContext) => {
    const session = new voice.AgentSession({
      stt: new inference.STT({
        model: 'deepgram/nova-3',
        language: 'en',
      }),

      llm: new inference.LLM({
        model: 'openai/gpt-5',
      }),

      tts: new inference.TTS({
        model: 'cartesia/sonic-3',
        voice: '9626c31c-bec5-4cca-baa8-f8ba9e84c8bc',
      }),

      turnDetection: new livekit.turnDetector.MultilingualModel(),
      vad: ctx.proc.userData.vad! as silero.VAD,
      voiceOptions: {
        preemptiveGeneration: true,
      },
    });

    const usageCollector = new metrics.UsageCollector();
    session.on(voice.AgentSessionEventTypes.MetricsCollected, (ev) => {
      metrics.logMetrics(ev.metrics);
      usageCollector.collect(ev.metrics);
    });

    const logUsage = async () => {
      const summary = usageCollector.getSummary();
      console.log(`Usage: ${JSON.stringify(summary)}`);
    };

    ctx.addShutdownCallback(logUsage);

    await session.start({
      agent: new Agent(),
      room: ctx.room,
      inputOptions: {
        noiseCancellation: BackgroundVoiceCancellation(),
      },
    });

    await ctx.connect();

    // Kick off the intake flow (baby steps: first name, last name, age, gender).
    // The Agent's instructions + tools will handle storing values into draftPatient
    // and confirming via confirmRegistration when complete.
   
      session.generateReply({
  instructions: `Greet the caller warmly.
Then begin patient registration.

Collect required fields first, one question at a time:
first name, last name, date of birth in MM slash DD slash YYYY, sex, phone number, address line 1, city, state, zip code.

After required fields are done, say:
I can also collect your email, insurance information, emergency contact, and preferred language. Would you like to provide any of those

If yes, collect whichever optional fields they want.

Before finishing, read back everything collected and ask for confirmation.
If they confirm, call confirmRegistration, then close politely.`,
});

  },
});

cli.runApp(
  new ServerOptions({
    agent: fileURLToPath(import.meta.url),

    // Keep this only if you are using explicit dispatch (SIP dispatch rules / AgentDispatch).
    // If you want automatic dispatch for every room, remove agentName.
    agentName: 'Personal-info-agent-dev',
  }),
);