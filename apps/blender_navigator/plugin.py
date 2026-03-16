bl_info = {
    "name": "Gemini Tuteur Visual Overlay",
    "author": "Antigravity/Openclaw",
    "version": (0, 2),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Gemini",
    "description": "Visual Tutor with real-time command polling",
    "category": "Interface",
}

import bpy
import gpu
import bgl
import json
import os
from gpu_extras.batch import batch_for_shader

# Path for communication
CMD_FILE = r"C:\Openclaw\apps\blender_navigator\commands.json"

# Storage for active overlays
overlays = []

def draw_callback_px(self, context):
    """Draw rectangles on the screen."""
    if not overlays:
        return

    shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')
    
    for item in overlays:
        # Expected pos: [x, y, w, h] in pixels
        try:
            x, y, w, h = item['pos']
            color = item.get('color', (0.1, 0.8, 0.2, 0.4)) # Default green
            
            # Vertices for the rectangle
            vertices = [
                (x, y), (x + w, y),
                (x, y + h), (x + w, y + h)
            ]
            indices = ((0, 1, 2), (1, 3, 2))
            
            batch = batch_for_shader(shader, 'TRIS', {"pos": vertices}, indices=indices)
            
            shader.bind()
            shader.uniform_float("color", color)
            
            bgl.glEnable(bgl.GL_BLEND)
            batch.draw(shader)
            bgl.glDisable(bgl.GL_BLEND)
        except Exception:
            pass

def poll_commands():
    """Timer function to check for new visual commands."""
    global overlays
    if os.path.exists(CMD_FILE):
        try:
            with open(CMD_FILE, 'r') as f:
                data = json.load(f)
                if data.get("refresh", False):
                    overlays = data.get("overlays", [])
                    # Mark as read
                    # We don't delete the file to avoid race conditions, 
                    # but we could clear the 'refresh' flag.
        except Exception as e:
            print(f"Error reading commands: {e}")
    return 0.5 # Check every 0.5 seconds

class GEMINI_PT_panel(bpy.types.Panel):
    bl_label = "Gemini Tuteur"
    bl_idname = "GEMINI_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Gemini'

    def draw(self, draw_self):
        layout = draw_self.layout
        layout.label(text="Statut: Connecté" if os.path.exists(CMD_FILE) else "Statut: En attente...")
        layout.operator("gemini.clear_overlays", text="Effacer les guides")

class GEMINI_OT_clear(bpy.types.Operator):
    bl_idname = "gemini.clear_overlays"
    bl_label = "Clear Overlays"
    def execute(self, context):
        global overlays
        overlays.clear()
        # Also clear the file
        if os.path.exists(CMD_FILE):
            with open(CMD_FILE, 'w') as f:
                json.dump({"refresh": False, "overlays": []}, f)
        return {'FINISHED'}

_handle = None

def register():
    global _handle
    bpy.utils.register_class(GEMINI_PT_panel)
    bpy.utils.register_class(GEMINI_OT_clear)
    
    _handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback_px, (None, None), 'WINDOW', 'POST_PIXEL')
    
    # Start polling
    if not bpy.app.timers.is_registered(poll_commands):
        bpy.app.timers.register(poll_commands)

def unregister():
    global _handle
    bpy.utils.unregister_class(GEMINI_PT_panel)
    bpy.utils.unregister_class(GEMINI_OT_clear)
    
    if _handle:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
    
    if bpy.app.timers.is_registered(poll_commands):
        bpy.app.timers.unregister(poll_commands)

if __name__ == "__main__":
    register()
