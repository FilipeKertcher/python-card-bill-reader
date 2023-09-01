from ultralytics import YOLO

# Use the model
# results = model.train(data="./tables-refactor.v4/data.yaml", epochs=1024)  # train the model
# results = model.val() # evaluate model performance on the validation set

def trainYOLO():
    model = YOLO("yolov8n.yaml")  # build a new model from scratch
    model = YOLO("./weights/best-512-improved.pt") #load a pretrained model (recommended for training)
    model.train(data="./tables-refactor.v5/data.yaml", epochs=1024)
    model.val()
    
    
trainYOLO()