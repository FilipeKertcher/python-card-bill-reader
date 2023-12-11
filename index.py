from functools import reduce
from ultralytics import YOLO
import boto3
import os, shutil
import json 
import psycopg2
from pdf2image import convert_from_bytes, convert_from_path
import re
from flask import Flask, flash, request
from dotenv import load_dotenv

load_dotenv()

YOLO_OUTPUT_FOLDER = os.environ['YOLO_OUTPUT_FOLDER']
YOLO_PREDICTION_RESULTS_FOLDER = os.environ['YOLO_PREDICTION_RESULTS_FOLDER']

connection = psycopg2.connect(
    user=os.environ['DB_USER'],
    password=os.environ['DB_PASSWORD'],
    host=os.environ['DB_HOST'],
    port=os.environ['DB_PORT'],
    database=os.environ['DB_NAME'],
)

db = connection.cursor()

client = boto3.client(
    'textract', 
    region_name=os.environ['AWS_REGION_NAME'], 
    aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'] ,
    aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY']
)

def trainYOLO():
    model = YOLO("yolov8n.yaml")  # build a new model from scratch
    model = YOLO("./weights/best-512-improved.pt") #load a pretrained model (recommended for training)
    model.train(data="./training-datasets/data.yaml", epochs=1024)
    model.val()

def analyzeImage(imagesPath):
    # # Load a model
    model = YOLO("yolov8n.yaml")  # build a new model from scratch
    model = YOLO("./weights/best-512-improved.pt") #load a pretrained model (recommended for training) 
    model.predict(
        source=imagesPath, 
        save_crop = True,
        project= YOLO_OUTPUT_FOLDER
    )  # predict on an image

def extractText():
    crops_path = f'{YOLO_PREDICTION_RESULTS_FOLDER}/Tables'
    files = os.listdir(crops_path)

    for item in files:
       file_url = f'{crops_path}/{item}'
       with open(file_url, 'rb') as file:
        img_test = file.read()
        bytes_test = bytearray(img_test)
        response = client.analyze_document(Document={'Bytes': bytes_test},FeatureTypes = ['TABLES'])
        
        stringified = json.dumps(response, indent=4)
        json_file = open(f'./ocr/{item.replace(".", "-")}.json', 'x')
        json_file.write(stringified)
        json_file.close()

def catalogRows(cells, blocks):
    rows = []

    for cell in cells:
        rowIndex = cell['RowIndex']
        columnIndex = cell['ColumnIndex']
        type= cell['EntityTypes'][0] if cell.get('EntityTypes') is not None  else None

        if('Relationships' not in cell):   
            continue

        child_items = list(filter((lambda x: x['Type'] == 'CHILD'), cell['Relationships']))[0]['Ids']
        children = [item for item in blocks if item['Id'] in child_items]
        
        
        # Add handling for rows with more than 3 cells (merged tables)
        filtered = list(filter((lambda x: 'Text' in x), children))
        texts = list(map((lambda item: item['Text']), filtered))

        joined = ' '.join(texts)
        cellItem = {
            'cellId': cell['Id'],
            'children': texts,
            'type': type,
            'joinedText': joined,
            'columnIndex': columnIndex
        }
        
        try:
            equivalent = [y['rowIndex'] for y in rows].index(rowIndex)
            rows[equivalent]['rowItems'].append(cellItem)
        except ValueError:
            obj = {
                'rowIndex': rowIndex,
                'rowItems':  [
                    cellItem
                ]
            }
            rows.append(obj)

    app.logger.info(json.dumps(rows))
    for row in rows:
        if(len(row['rowItems']) < 3):   
            row['type'] = 'OUTSIDE_TABLE'
            continue

        base_type = row['rowItems'][0]['type']
        
        if(base_type is not None): 
            continue
        mapped = list(map((lambda item: item['joinedText']), row['rowItems']))
        joined = " ".join(mapped)

        if('LANÇAMENTOS NO CARTÃO' in joined.upper()):
            row['type'] = 'OUTSIDE_TABLE'
        else:
            row['type'] = 'TABLE_VALUE'
            
    return rows

def transformRow(row): 
    items = sorted(row['rowItems'], key=lambda x: x['columnIndex'], reverse=False)
    texts = list(map(lambda item: item['joinedText'],items))

    texts = list(filter((
        lambda x: 
            x != 'Pontos transferidos ao parceiro' and 
            x != 'DATA ESTABELECIMENTO'
    ), texts))
     
    if(len(row['rowItems']) == 1):
        print(row)
    elif len(row['rowItems']) == 2:
        date = "NOT_FOUND"
        placeName, price = texts
    elif len(row['rowItems']) > 3:
        date, placeName, price = texts[0:3]
    else:
        date, placeName, price = texts

        date = re.sub(r"[!@#$%^&*,) a-z]", "",date)

    placeName= placeName.replace('SAO PAULO', 'SP')
    reversedArray = placeName.split(' ')[::-1]

    if(len(reversedArray) == 1):
       cardLocation = None 
       cardCategory = None
       placeNameArray  = reversedArray
    else:
        cardLocation, cardCategory, *placeNameArray = reversedArray

    placeTreatedName = ' '.join(placeNameArray[::-1])

    replaced=price.replace('.', '').replace(',','.')

    return {
        'date': date.replace('@ ', '').replace('<','').replace(')))) ', ''),
        'expenseFullName': placeName,
        "placeName": placeTreatedName,
        "cardInfo": {
            "cardLocation": cardLocation,
            "cardCategory": cardCategory
        },
        'amount': replaced
    }

def parseOCRResult(ocrResponse): 
    blocks = ocrResponse['Blocks']
    cells=[]
    
    for block in blocks:
        if block['BlockType'] == 'CELL':
            cells.append(block)

    mappedRows = catalogRows(cells, blocks)

    value_items = list(filter((lambda x:  'type'in x and x['type'] == 'TABLE_VALUE'), mappedRows))

    pricedItems = list(map(lambda x: transformRow(x), value_items))

    searchItems = list(map(lambda x: x['placeName'].lower(),pricedItems))
    
    sql = '''
        SELECT 
            pt.id, 
            pt.tag as tag_value, 
            p.name as place_name 
        FROM place_tags pt 
        join places p on p.id = pt.place_id 
        WHERE LOWER(tag) = ANY (%(tags)s)
    '''

    db.execute(sql, {'tags': searchItems})
    founds = db.fetchall()

    for item in pricedItems:
        dbRecord = [record for record in founds if record[1] == item['placeName'] ]
        
        friendlyName = dbRecord[0][2] if len(dbRecord) > 0 else None
        item['friendlyName'] = friendlyName
    return pricedItems
   
def loadAndExtract():  
    files = os.scandir('./ocr')

    finalResults = []

    for entry in files:
       file = open(f'./ocr/{entry.name}')
       ocrResponse = parseOCRResult(json.load(file))
       finalResults.append(ocrResponse)

    merged = reduce(lambda a, b: a+b, finalResults)
    return json.dumps(merged)

def convertPDFToJPGFromRequest(file):
    
    documentPassword = os.environ['PDF_DOCUMENT_PASSWORD']

    images = convert_from_bytes(
        file.read(),  
        output_folder='./runs/', 
        userpw=documentPassword, 
        fmt='jpg',
        output_file="image"
    )
    
    files = os.listdir('./runs')
    mapped=list(map(lambda file: f'./runs/{file}', files))
    app.logger.info('Converted %s images, calling analyze', len(images))
    
    analyzeImage(mapped)

def cleanOutputFolders(): 
    app.logger.info('Starting to clean Folders')
    
    runs_path = './runs'
    results_path = './results'
    ocr_path = './ocr'
    
    if(os.path.exists(runs_path)):
        shutil.rmtree('./runs')
    
    if(os.path.exists(ocr_path)):
        shutil.rmtree('./ocr')

    if(os.path.exists(results_path)):
        shutil.rmtree('./results')
    
    
    os.mkdir('./runs')
    os.mkdir('./ocr')
    os.mkdir('./results')  

def convertPDFToJPG():
    
    convert_from_path(
        './items-to-process/Fatura_Mastercard_100471492916_04-2023.pdf',  
        output_folder='./runs/', 
        userpw='', 
        fmt='jpg',
        output_file="image"
    )
    
    files = os.listdir('./runs')

    mapped=list(map(lambda file: f'./runs/{file}', files))
    print(mapped)
    
    analyzeImage(mapped)
    return

#loadAndExtract()
#convertPDFToJPGFromRequest()
#extractText()
#analyzeImage()

## 1st - convertPDFToJPG
## 2nd - extractText
## 3rd - loadAndExtract

app = Flask(__name__)

@app.route('/teste')
def hello():
    return 'Hello, World!'

@app.route('/analyze-document', methods = ['POST'])
def analyzeDocument():
    if 'file' not in request.files:
        flash('No file part')
        return  "no-file"
    file = request.files['file']
    app.logger.info(file)
    
    convertPDFToJPGFromRequest(file)
    extractText()
    
    results = loadAndExtract()
    return results
        
@app.route('/clean-folders', methods = ['POST'])
def cleanFolders():
    # The purpose of this endpoint is just to clean the results folder, pretty primitive way of dealing with the cleanup issue
    app.logger.info('STARTING')
    cleanOutputFolders()
    return "called"

@app.route('/custom-method', methods = ['POST'])
def customMethod():
    app.logger.info('STARTING')
    trainYOLO()
    return "called"

if __name__ == '__main__':
    app.secret_key = 'BABY_DONT_HURT_ME'
    app.run(debug=True, host='0.0.0.0')
