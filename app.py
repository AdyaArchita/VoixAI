import os
import json
import logging
import time
import shutil
import ollama
import gradio as gr
from faster_whisper import WhisperModel
from datetime import datetime

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

OUTPUT_DIR = "./output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- AI Agent Class ---
class LocalAIAgent:
    def __init__(self, model_size="base", llm_model="llama3.2"):
        self.llm_model = llm_model
        self.history = []
        
        # Initialize STT Model (Faster-Whisper)
        try:
            logger.info(f"Loading Faster-Whisper model ({model_size})...")
            # Using CPU for maximum compatibility, change to "cuda" if GPU is available
            self.stt_model = WhisperModel(model_size, device="cpu", compute_type="int8")
            logger.info("STT Model loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load STT model: {e}")
            self.stt_model = None

    def transcribe(self, audio_path):
        """Transcribes audio file to text using Faster-Whisper."""
        if not self.stt_model:
            return "Error: STT model not loaded."
        if not audio_path:
            return ""
            
        try:
            # Force language to English ('en') and enable VAD filter to ignore non-speech noise (booms, clicks, etc.)
            segments, info = self.stt_model.transcribe(
                audio_path, 
                beam_size=5, 
                language="en",
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500)
            )
            transcription = "".join([segment.text for segment in segments])
            return transcription.strip()
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return f"Error during transcription: {str(e)}"

    def classify_intent(self, text):
        """Uses Ollama to classify intent and extract parameters in structured JSON."""
        system_prompt = """
        You are a strict JSON Intent Classifier. Your ONLY output must be a valid JSON list of objects.
        Do not include ANY introductory text, greetings, code blocks, or markdown formatting.
        
        Supported Intents:
        1. 'create_file': params { "filename": "name.txt", "content": "text content" }
        2. 'write_code': params { "filename": "script.py", "code": "code block" }
        3. 'summarize': params { "text": "content to summarize" }
        4. 'chat': params { "response": "your helpful chat response" }
        
        Rules:
        - Support multiple actions if the user asks for more than one thing.
        - RELATIVE filenames only. No 'output/' in filename.
        - Return ONLY the JSON array. Example: [{"intent": "create_file", "params": {...}}]
        """
        
        try:
            response = ollama.chat(model=self.llm_model, messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': f"Classify this request: {text}"}
            ])
            
            content = response['message']['content'].strip()
            logger.info(f"LLM Response: {content}")

            # 1. Clean markdown code blocks if present
            if "```" in content:
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.split("```")[0].strip()

            # 2. Extract JSON array using brackets if there's surrounding text
            start_idx = content.find("[")
            end_idx = content.rfind("]")
            
            if start_idx != -1 and end_idx != -1:
                content = content[start_idx:end_idx+1]
                
            actions = json.loads(content)
            return actions
            
        except Exception as e:
            logger.error(f"Intent classification error: {e}")
            # Fallback to chat if JSON parsing fails completely
            fallback_response = "I understood your request but had trouble formatting the actions. Let's chat instead: " + (content[:100] if 'content' in locals() else "Unknown error.")
            return [{"intent": "chat", "params": {"response": fallback_response}}]

    def execute_action(self, action):
        """Executes a single action within the safety-constrained output folder."""
        intent = action.get("intent")
        params = action.get("params", {})
        
        try:
            if intent == "create_file":
                filename = os.path.basename(params.get("filename", "untitled.txt"))
                filepath = os.path.join(OUTPUT_DIR, filename)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(params.get("content", ""))
                return f"✅ Created file: {filename}"
                
            elif intent == "write_code":
                filename = os.path.basename(params.get("filename", "script.py"))
                filepath = os.path.join(OUTPUT_DIR, filename)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(params.get("code", ""))
                return f"💻 Wrote code to: {filename}"
                
            elif intent == "summarize":
                text = params.get("text", "")
                summary_prompt = f"Summarize the following text concisely:\n\n{text}"
                resp = ollama.generate(model=self.llm_model, prompt=summary_prompt)
                return f"📝 Summary: {resp['response']}"
                
            elif intent == "chat":
                return params.get("response", "No response generated.")
                
            else:
                return f"⚠️ Unknown intent: {intent}"
        except Exception as e:
            logger.error(f"Execution error: {e}")
            return f"❌ Error executing {intent}: {str(e)}"

# --- UI Logic ---
agent = LocalAIAgent()

def process_pipeline(audio_input, file_upload):
    source = audio_input if audio_input else file_upload
    if not source:
        return "No input provided.", "None", "None", "Waiting for input..."
    
    # 1. Transcription
    transcription = agent.transcribe(source)
    if not transcription or transcription.startswith("Error"):
        return transcription, "N/A", "N/A", "Failed at transcription."
    
    # 2. Intent Parsing
    actions = agent.classify_intent(transcription)
    
    # 3. Preparation for UI
    intent_names = ", ".join([a['intent'] for a in actions])
    
    # We return the actions as state for the confirmation step
    return transcription, intent_names, actions, "Awaiting Confirmation..."

def confirm_execution(actions):
    if not actions or actions == "None":
        return "No actions to execute."
        
    results = []
    for action in actions:
        result = agent.execute_action(action)
        results.append(result)
        
    return "\n".join(results)

# --- Gradio UI Layout ---
with gr.Blocks() as demo:
    gr.Markdown("# 🎙️ VoixAI: Local Voice-Controlled Agent")
    gr.Markdown("Transform your voice into local actions. 100% private, 100% local.")
    
    with gr.Row():
        with gr.Column(scale=1):
            audio_mic = gr.Audio(sources="microphone", type="filepath", label="Speak to Me")
            audio_file = gr.Audio(sources="upload", type="filepath", label="Upload Audio (.wav/.mp3)")
            btn_process = gr.Button("🚀 Process Input", variant="primary")
            
        with gr.Column(scale=2):
            with gr.Group():
                txt_transcription = gr.Textbox(label="1. Transcribed Text", interactive=False)
                txt_intent = gr.Textbox(label="2. Detected Intent", interactive=False)
                
            with gr.Accordion("Pipeline States (JSON)", open=False):
                state_actions = gr.JSON(label="Proposed Actions")
                
            txt_result = gr.Textbox(label="3. Action Result", lines=5, interactive=False)
            
            with gr.Row():
                btn_confirm = gr.Button("✅ Confirm & Execute", variant="secondary")
                btn_clear = gr.Button("🗑️ Clear")

    # Wire up the logic
    btn_process.click(
        fn=process_pipeline,
        inputs=[audio_mic, audio_file],
        outputs=[txt_transcription, txt_intent, state_actions, txt_result]
    )
    
    btn_confirm.click(
        fn=confirm_execution,
        inputs=[state_actions],
        outputs=[txt_result]
    )
    
    btn_clear.click(lambda: [None, None, "", "", None, "Waiting..."], outputs=[audio_mic, audio_file, txt_transcription, txt_intent, state_actions, txt_result])

if __name__ == "__main__":
    demo.launch(theme=gr.themes.Soft(primary_hue="blue", neutral_hue="slate"))
