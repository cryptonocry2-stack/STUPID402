// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/Strings.sol";

contract MyNFT is ERC721, Ownable {
    uint256 public constant MAX_SUPPLY = 3000;
    uint256 public currentTokenId;
    string public baseURI;
    string public unrevealedURI;
    bool public revealed = false;
    
    constructor(
        string memory name,
        string memory symbol,
        string memory initialBaseURI,
        string memory _unrevealedURI
    ) ERC721(name, symbol) Ownable(msg.sender) {
        baseURI = initialBaseURI;
        unrevealedURI = _unrevealedURI;
    }
    
    function mint(address to) external onlyOwner {
        require(currentTokenId < MAX_SUPPLY, "Max supply reached");
        
        currentTokenId++;
        _safeMint(to, currentTokenId);
    }
    
    function tokenURI(uint256 tokenId) public view virtual override returns (string memory) {
        require(ownerOf(tokenId) != address(0), "Token doesn't exist");
        
        // Если не revealed, возвращаем unrevealed URI для всех
        if (!revealed) {
            return unrevealedURI;
        }
        
        // Если revealed, возвращаем уникальный URI для каждого токена
        return string(abi.encodePacked(baseURI, Strings.toString(tokenId)));
    }
    
    function reveal() external onlyOwner {
        revealed = true;
    }
    
    function setBaseURI(string memory newBaseURI) external onlyOwner {
        baseURI = newBaseURI;
    }
    
    function setUnrevealedURI(string memory _unrevealedURI) external onlyOwner {
        unrevealedURI = _unrevealedURI;
    }
}

